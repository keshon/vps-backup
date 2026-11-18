package backup

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type SavedApp struct {
	ID        string `json:"id"`
	Archive   string `json:"archive"`
	Bytes     int64  `json:"data_added"`
	Unchanged bool   `json:"unchanged"`
}

type Report struct {
	Status   string     `json:"status"`
	ServerID string     `json:"server_id"`
	Apps     []SavedApp `json:"apps"`
	Error    string     `json:"error,omitempty"`
	Finished time.Time  `json:"finished"`
}

type appState struct {
	Fingerprint string
	Archive     string
	Remote      string
	Bytes       int64
}

func Run(ctx context.Context, cfg Config, encoded string, progress func(string)) (report Report, runErr error) {
	report = Report{Status: "failed", ServerID: cfg.ServerID}
	err := cfg.Validate()
	if err != nil {
		return report, err
	}
	if len(cfg.Apps) == 0 {
		return report, fmt.Errorf("add at least one app before backing up")
	}

	env, err := cloudEnvironment(encoded)
	if err != nil {
		return report, err
	}
	err = ensureRclone(ctx, progress)
	if err != nil {
		return report, err
	}

	err = os.MkdirAll(filepath.Join(cfg.StateDir, "apps"), 0o700)
	if err != nil {
		return report, fmt.Errorf("opening working folder: %w", err)
	}
	lock, err := lockState(cfg.StateDir)
	if err != nil {
		return report, err
	}
	defer lock.Close()

	work, err := os.MkdirTemp(cfg.StateDir, "run-")
	if err != nil {
		return report, err
	}
	defer func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		restartErr := restartContainers(cleanupCtx, cfg.StateDir)
		removeErr := os.RemoveAll(work)
		runErr = errors.Join(runErr, restartErr, removeErr)
		if runErr == nil {
			report.Status = "success"
		} else {
			report.Error = runErr.Error()
		}
		report.Finished = time.Now()
		saveErr := writeJSON(filepath.Join(cfg.StateDir, "last-run.json"), report)
		runErr = errors.Join(runErr, saveErr)
	}()

	err = restartContainers(ctx, cfg.StateDir)
	if err != nil {
		return report, err
	}
	for _, app := range cfg.Apps {
		progress(app.ID + ": checking source and preparing archive")
		saved, err := backupApp(ctx, cfg, app, env, work, progress)
		if err != nil {
			return report, fmt.Errorf("%s: %w", app.ID, err)
		}
		report.Apps = append(report.Apps, saved)
	}
	return report, nil
}

func backupApp(ctx context.Context, cfg Config, app App, env []string, work string, progress func(string)) (SavedApp, error) {
	path, err := filepath.EvalSymlinks(app.Path)
	if err != nil {
		return SavedApp{}, fmt.Errorf("opening source folder: %w", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		return SavedApp{}, err
	}
	if !info.IsDir() {
		return SavedApp{}, fmt.Errorf("choose a source folder, not a file")
	}

	state, err := filepath.EvalSymlinks(cfg.StateDir)
	if err != nil {
		return SavedApp{}, err
	}
	if within(path, state) {
		return SavedApp{}, fmt.Errorf("working folder is inside source; choose a separate working folder")
	}
	if within(state, path) {
		return SavedApp{}, fmt.Errorf("source is inside the working folder; choose a separate source")
	}
	app.Path = path

	err = stopContainers(ctx, cfg.StateDir, app.StopContainers)
	if err != nil {
		return SavedApp{}, err
	}
	fingerprint, size, err := archive(ctx, app, io.Discard)
	if err != nil {
		return SavedApp{}, err
	}

	remote := strings.TrimRight(cfg.Remote, "/") + "/" + cfg.ServerID + "/" + app.ID
	statePath := filepath.Join(cfg.StateDir, "apps", app.ID+".json")
	var previous appState
	raw, err := os.ReadFile(statePath)
	if err == nil {
		err = json.Unmarshal(raw, &previous)
		if err != nil {
			return SavedApp{}, fmt.Errorf("reading previous archive state: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return SavedApp{}, err
	}

	if previous.Fingerprint == fingerprint && previous.Remote == remote {

		err = restartContainers(ctx, cfg.StateDir)
		if err != nil {
			return SavedApp{}, err
		}
		var stat struct{ Size int64 }
		var output strings.Builder
		err = cloud(ctx, env, nil, &output, "lsjson", remote+"/"+previous.Archive, "--stat")
		if err != nil {
			return SavedApp{}, fmt.Errorf("checking last archive: %w; check cloud access before trying again", err)
		}
		err = json.Unmarshal([]byte(output.String()), &stat)
		if err != nil {
			return SavedApp{}, err
		}
		if stat.Size != previous.Bytes {
			return SavedApp{}, fmt.Errorf("last cloud archive changed size; inspect cloud storage before continuing")
		}
		err = retain(ctx, env, remote, previous.Archive, cfg.Keep)
		return SavedApp{ID: app.ID, Archive: previous.Archive, Unchanged: true}, err
	}

	free, err := freeSpace(cfg.StateDir)
	if err != nil {
		return SavedApp{}, err
	}
	if free < size+size/10+64*1024*1024+cfg.ReserveMiB*1024*1024 {
		return SavedApp{}, fmt.Errorf("not enough scratch space above the %d MiB reserve; free space or change working folder", cfg.ReserveMiB)
	}

	var random [8]byte
	_, err = rand.Read(random[:])
	if err != nil {
		return SavedApp{}, err
	}
	name := time.Now().UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(random[:]) + ".tar.gz"
	local := filepath.Join(work, name)
	created, err := compressedArchive(ctx, app, local)
	if err != nil {
		return SavedApp{}, err
	}
	if created != fingerprint {
		return SavedApp{}, fmt.Errorf("source changed during archiving; configure all writer containers to stop")
	}

	err = restartContainers(ctx, cfg.StateDir)
	if err != nil {
		return SavedApp{}, err
	}

	progress(app.ID + ": uploading and verifying; containers have restarted")
	bytes, err := upload(ctx, env, local, remote, name)
	if err != nil {
		return SavedApp{}, err
	}
	err = writeJSON(statePath, appState{Fingerprint: fingerprint, Archive: name, Remote: remote, Bytes: bytes})
	if err != nil {
		return SavedApp{}, err
	}

	err = retain(ctx, env, remote, name, cfg.Keep)
	if err != nil {
		return SavedApp{}, err
	}
	err = os.Remove(local)
	return SavedApp{ID: app.ID, Archive: name, Bytes: bytes}, err
}

func within(parent, child string) bool {
	rel, err := filepath.Rel(parent, child)
	if err != nil {
		return false
	} // Windows sources may live on another volume.
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator)))
}

func stopContainers(ctx context.Context, state string, names []string) error {
	stopped := []string{}
	for _, name := range names {
		output, err := exec.CommandContext(ctx, "docker", "inspect", "--format", "{{.State.Running}}", name).Output()
		if err != nil {
			return fmt.Errorf("checking container %s: %w", name, err)
		}
		if strings.TrimSpace(string(output)) != "true" {
			continue
		}

		stopped = append(stopped, name)
		err = writeJSON(filepath.Join(state, "stopped-containers.json"), stopped)
		if err != nil {
			return err
		}
		err = exec.CommandContext(ctx, "docker", "stop", "--time", "30", name).Run()
		if err != nil {
			return fmt.Errorf("stopping container %s: %w", name, err)
		}
	}
	return nil
}

func restartContainers(ctx context.Context, state string) error {
	path := filepath.Join(state, "stopped-containers.json")
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}

	var names []string
	err = json.Unmarshal(raw, &names)
	if err != nil {
		return err
	}

	var restartErr error
	for _, name := range names {
		err = exec.CommandContext(ctx, "docker", "start", name).Run()
		if err != nil {
			restartErr = errors.Join(restartErr, fmt.Errorf("restart %s manually with docker start %s: %w", name, name, err))
		}
	}
	if restartErr != nil {
		return restartErr
	}
	return os.Remove(path)
}
