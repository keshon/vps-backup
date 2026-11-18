package backup

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
)

type App struct {
	ID             string   `json:"id"`
	Path           string   `json:"path"`
	StopContainers []string `json:"stop_containers,omitempty"`
	Excludes       []string `json:"excludes,omitempty"`
}

type Config struct {
	ServerID   string `json:"server_id"`
	Remote     string `json:"remote"`
	StateDir   string `json:"state_dir"`
	Keep       int    `json:"keep"`
	ReserveMiB int64  `json:"reserve_mib"`
	Apps       []App  `json:"apps"`
}

var identifier = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`)

func Default() (Config, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return Config{}, err
	}
	host, err := os.Hostname()
	if err != nil {
		return Config{}, err
	}
	return Config{ServerID: host, StateDir: filepath.Join(dir, "vps-backup", "state"), Keep: 3, ReserveMiB: 512}, nil
}

func Load(path string) (Config, error) {
	cfg, err := Default()
	if err != nil {
		return Config{}, err
	}

	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return cfg, nil
	}
	if err != nil {
		return Config{}, fmt.Errorf("reading settings: %w", err)
	}

	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	err = decoder.Decode(&cfg)
	if err != nil {
		return Config{}, fmt.Errorf("reading %s: %w; keep this file and correct its JSON", path, err)
	}
	return cfg, nil
}

func (c Config) Validate() error {
	if !identifier.MatchString(c.ServerID) {
		return fmt.Errorf("choose a server name using letters, digits, dots, underscores or hyphens")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9_.-]+:[^\r\n]+$`).MatchString(c.Remote) {
		return fmt.Errorf("set a cloud destination such as union-koofr:backups")
	}

	if c.Keep < 1 || c.ReserveMiB < 1 || c.ReserveMiB > 1<<30 {
		return fmt.Errorf("keep at least one archive and reserve at least 1 MiB")
	}
	if !filepath.IsAbs(c.StateDir) {
		return fmt.Errorf("choose an absolute working folder")
	}

	seen := make(map[string]bool)
	for _, app := range c.Apps {
		if !identifier.MatchString(app.ID) || seen[app.ID] {
			return fmt.Errorf("app names must be unique and use letters, digits, dots, underscores or hyphens: %q", app.ID)
		}
		seen[app.ID] = true
		if !filepath.IsAbs(app.Path) {
			return fmt.Errorf("%s: choose an absolute source folder", app.ID)
		}

		for _, name := range app.StopContainers {
			if !identifier.MatchString(name) {
				return fmt.Errorf("%s: invalid container name %q", app.ID, name)
			}
		}

		for _, pattern := range app.Excludes {
			_, err := filepath.Match(pattern, "test")
			if err != nil {
				return fmt.Errorf("%s: invalid exclude pattern %q", app.ID, pattern)
			}
		}
	}
	return nil
}

func Save(path string, cfg Config) error {
	err := cfg.Validate()
	if err != nil {
		return err
	}
	return writeJSON(path, cfg)
}

func writeJSON(path string, value any) error {
	err := os.MkdirAll(filepath.Dir(path), 0o700)
	if err != nil {
		return err
	}

	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".save-*")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name) // Rename consumes the temporary file on success.

	_, err = file.Write(append(data, '\n'))
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}

	return os.Rename(name, path)
}

func LastReport(stateDir string) (Report, error) {
	raw, err := os.ReadFile(filepath.Join(stateDir, "last-run.json"))
	if err != nil {
		return Report{}, err
	}
	var report Report
	err = json.Unmarshal(raw, &report)
	return report, err
}
