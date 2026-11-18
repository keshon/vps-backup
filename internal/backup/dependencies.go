package backup

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
)

func ensureRclone(ctx context.Context, progress func(string)) error {
	_, err := exec.LookPath("rclone")
	if err == nil {
		return nil
	}
	if runtime.GOOS != "linux" || os.Geteuid() != 0 {
		return fmt.Errorf("rclone is missing; on Ubuntu run sudo apt-get install -y rclone, then try again")
	}
	release, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return err
	}
	if !strings.Contains("\n"+string(release), "\nID=ubuntu\n") {
		return fmt.Errorf("automatic tool installation supports Ubuntu; install rclone using your package manager")
	}
	progress("Installing missing tool: rclone (apt). No backup service or timer is installed.")
	for _, args := range [][]string{{"update"}, {"install", "-y", "--no-install-recommends", "rclone", "ca-certificates"}} {
		cmd := exec.CommandContext(ctx, "apt-get", args...)
		cmd.Env = append(os.Environ(), "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_MODE=l")
		var output bytes.Buffer
		cmd.Stdout, cmd.Stderr = &output, &output
		err = cmd.Run()
		if err != nil {
			return fmt.Errorf("installing rclone: %w\n%s", err, output.String())
		}
	}
	return nil
}
