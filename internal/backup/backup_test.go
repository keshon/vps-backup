package backup

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func sourceFixture(t *testing.T) App {
	t.Helper()
	dir := t.TempDir()
	err := os.WriteFile(filepath.Join(dir, "data"), []byte("restore these bytes"), 0o640)
	if err != nil {
		t.Fatal(err)
	}
	err = os.WriteFile(filepath.Join(dir, "ignored.log"), []byte("skip"), 0o600)
	if err != nil {
		t.Fatal(err)
	}
	err = os.Mkdir(filepath.Join(dir, "empty"), 0o750)
	if err != nil {
		t.Fatal(err)
	}
	return App{ID: "sample", Path: dir, Excludes: []string{"*.log"}}
}

func TestArchiveRoundtripAndFingerprint(t *testing.T) {
	app := sourceFixture(t)
	ctx := context.Background()
	first, _, err := archive(ctx, app, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "backup.tar.gz")
	second, err := compressedArchive(ctx, app, path)
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Fatal("unchanged files produced different fingerprints")
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	gz, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	seen := map[string]bool{}
	for {
		header, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		seen[header.Name] = true
		if header.Name == "data" {
			raw, err := io.ReadAll(tr)
			if err != nil {
				t.Fatal(err)
			}
			if string(raw) != "restore these bytes" {
				t.Fatal("wrong restored content")
			}
		}
	}
	if !seen["data"] || !seen["empty"] || seen["ignored.log"] {
		t.Fatal(seen)
	}
	err = os.WriteFile(filepath.Join(app.Path, "data"), []byte("changed"), 0o640)
	if err != nil {
		t.Fatal(err)
	}
	third, _, err := archive(ctx, app, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if third == first {
		t.Fatal("content change was missed")
	}
	ctx, cancel := context.WithCancel(ctx)
	cancel()
	_, _, err = archive(ctx, app, io.Discard)
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestOSLockReleasesWithoutDeletingFile(t *testing.T) {
	dir := t.TempDir()
	first, err := lockState(dir)
	if err != nil {
		t.Fatal(err)
	}
	second, err := lockState(dir)
	if err == nil {
		second.Close()
		t.Fatal("second operation acquired the lock")
	}
	err = first.Close()
	if err != nil {
		t.Fatal(err)
	}
	third, err := lockState(dir)
	if err != nil {
		t.Fatal("lock did not release:", err)
	}
	err = third.Close()
	if err != nil {
		t.Fatal(err)
	}
}

func TestCredentialsAreLiteralEnvironmentOnly(t *testing.T) {
	encoded := base64.StdEncoding.EncodeToString([]byte("[union-koofr]\ntype = union\nupstreams = cloud-one: cloud-two:\n[cloud-one]\ntype = koofr\npassword = literal$(not-executed)\n"))
	env, err := cloudEnvironment(encoded)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(env, "\n")
	if !strings.Contains(joined, "RCLONE_CONFIG_UNION-KOOFR_TYPE=union") || !strings.Contains(joined, "literal$(not-executed)") {
		t.Fatal("configuration changed")
	}
	_, err = cloudEnvironment(base64.StdEncoding.EncodeToString([]byte("not a config")))
	if err == nil {
		t.Fatal("malformed credentials accepted")
	}
}

func TestRealRcloneBackupAndUnchangedRun(t *testing.T) {
	_, err := exec.LookPath("rclone")
	if err != nil {
		t.Skip("rclone not installed")
	}
	app := sourceFixture(t)
	cloudDir := t.TempDir()
	cfg := Config{ServerID: "fixture", Remote: "local:" + filepath.ToSlash(cloudDir), StateDir: t.TempDir(), Keep: 2, ReserveMiB: 1, Apps: []App{app}}
	encoded := base64.StdEncoding.EncodeToString([]byte("[local]\ntype = local\n"))
	for i := range 2 {
		report, err := Run(context.Background(), cfg, encoded, func(string) {})
		if err != nil {
			t.Fatal(err)
		}
		if report.Status != "success" || len(report.Apps) != 1 || report.Apps[0].Unchanged != (i == 1) {
			t.Fatalf("unexpected report: %+v", report)
		}
	}
	entries, err := os.ReadDir(filepath.Join(cloudDir, "fixture", "sample"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatal("expected an archive and checksum:", entries)
	}
	state, err := os.ReadFile(filepath.Join(cfg.StateDir, "last-run.json"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(state, []byte(encoded)) {
		t.Fatal("credentials persisted")
	}
	var report Report
	err = json.Unmarshal(state, &report)
	if err != nil {
		t.Fatal(err)
	}
}
