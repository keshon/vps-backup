package backup

import (
	"context"
	"encoding/base64"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestUploadFailureRestartsContainersAndPreservesRetry(t *testing.T) {
	_, err := exec.LookPath("rclone")
	if err != nil {
		t.Skip("rclone not installed")
	}
	bin := t.TempDir()
	running := filepath.Join(bin, "running")
	err = os.WriteFile(running, []byte("true"), 0o600)
	if err != nil {
		t.Fatal(err)
	}
	script := `#!/bin/sh
case "$1" in
  inspect) cat "$FIXTURE_RUNNING" ;;
  stop) printf false > "$FIXTURE_RUNNING" ;;
  start) [ "${FIXTURE_RESTART_FAIL:-0}" != 1 ] || exit 1; printf true > "$FIXTURE_RUNNING" ;;
  *) exit 1 ;;
esac
`
	err = os.WriteFile(filepath.Join(bin, "docker"), []byte(script), 0o700)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("FIXTURE_RUNNING", running)
	app := sourceFixture(t)
	app.StopContainers = []string{"fixture-db"}
	cfg := Config{ServerID: "fixture", Remote: "missing:backups", StateDir: t.TempDir(), Keep: 2, ReserveMiB: 1, Apps: []App{app}}
	encoded := base64.StdEncoding.EncodeToString([]byte("[local]\ntype = local\n"))
	_, err = Run(context.Background(), cfg, encoded, func(string) {})
	if err == nil {
		t.Fatal("missing cloud remote accepted")
	}
	raw, err := os.ReadFile(running)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "true" {
		t.Fatal("container was left stopped")
	}
	_, err = os.Stat(filepath.Join(cfg.StateDir, "apps", app.ID+".json"))
	if !os.IsNotExist(err) {
		t.Fatal("failed upload committed success")
	}
	entries, err := os.ReadDir(cfg.StateDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), "run-") {
			t.Fatal("scratch leaked")
		}
	}

	t.Setenv("FIXTURE_RESTART_FAIL", "1")
	_, err = Run(context.Background(), cfg, encoded, func(string) {})
	if err == nil {
		t.Fatal("failed restart accepted")
	}
	_, err = os.Stat(filepath.Join(cfg.StateDir, "stopped-containers.json"))
	if err != nil {
		t.Fatal("restart journal disappeared")
	}

	t.Setenv("FIXTURE_RESTART_FAIL", "0")
	cfg.Remote = "local:" + t.TempDir()
	report, err := Run(context.Background(), cfg, encoded, func(string) {})
	if err != nil {
		t.Fatal(err)
	}
	if report.Status != "success" {
		t.Fatal(report)
	}
	raw, err = os.ReadFile(running)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "true" {
		t.Fatal("journal recovery did not restart container")
	}
}
