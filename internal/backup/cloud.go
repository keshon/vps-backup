package backup

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"slices"
	"strings"
)

func cloudEnvironment(encoded string) ([]string, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(encoded))
	if err != nil {
		return nil, fmt.Errorf("cloud credentials must be a base64-encoded rclone config: %w", err)
	}

	env := []string{}
	for _, pair := range os.Environ() {
		if !strings.HasPrefix(pair, "RCLONE_") {
			env = append(env, pair)
		}
	}

	section := ""
	sectionName := regexp.MustCompile(`^[a-zA-Z0-9_.-]+$`)
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.TrimSuffix(strings.TrimPrefix(line, "["), "]")
			if !sectionName.MatchString(section) {
				return nil, fmt.Errorf("cloud config has an unsupported remote name")
			}
			continue
		}

		key, value, found := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if !found || section == "" || !regexp.MustCompile(`^[a-zA-Z0-9_]+$`).MatchString(key) {
			return nil, fmt.Errorf("cloud config needs ordinary [remote] and key = value entries")
		}
		env = append(env, "RCLONE_CONFIG_"+strings.ToUpper(section)+"_"+strings.ToUpper(key)+"="+strings.TrimSpace(value))
	}

	err = scanner.Err()
	if err != nil {
		return nil, err
	}
	if section == "" {
		return nil, fmt.Errorf("supply your rclone config before starting a backup")
	}
	return env, nil
}

func cloud(ctx context.Context, env []string, input io.Reader, output io.Writer, args ...string) error {
	args = append([]string{"--config", "/dev/null"}, args...)
	if os.DevNull != "/dev/null" {
		args[1] = os.DevNull
	}

	cmd := exec.CommandContext(ctx, "rclone", args...)
	cmd.Env, cmd.Stdin, cmd.Stdout = env, input, output
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		text := stderr.String()
		if len(text) > 4096 {
			text = text[len(text)-4096:]
		}
		return fmt.Errorf("rclone %s: %w\n%s", args[2], err, strings.TrimSpace(text))
	}
	return nil
}

func upload(ctx context.Context, env []string, local, remote, name string) (int64, error) {
	err := cloud(ctx, env, nil, io.Discard, "copyto", local, remote+"/"+name)
	if err != nil {
		return 0, err
	}

	file, err := os.Open(local)
	if err != nil {
		return 0, err
	}
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	closeErr := file.Close()
	if err != nil {
		return 0, err
	}
	if closeErr != nil {
		return 0, closeErr
	}

	expected := hex.EncodeToString(hash.Sum(nil))
	hash.Reset()
	err = cloud(ctx, env, nil, hash, "cat", remote+"/"+name)
	if err != nil {
		return 0, err
	}
	if hex.EncodeToString(hash.Sum(nil)) != expected {
		return 0, fmt.Errorf("uploaded archive failed SHA-256 verification; success was not recorded")
	}

	checksum := strings.NewReader(expected + "  " + name + "\n")
	err = cloud(ctx, env, checksum, io.Discard, "rcat", remote+"/"+name+".sha256")
	return size, err
}

var archiveName = regexp.MustCompile(`^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\.tar\.gz$`)

func retain(ctx context.Context, env []string, remote, latest string, keep int) error {
	var listing bytes.Buffer
	err := cloud(ctx, env, nil, &listing, "lsf", remote, "--files-only", "--max-depth", "1")
	if err != nil {
		return err
	}
	names := strings.Split(strings.TrimSpace(listing.String()), "\n")
	slices.Sort(names)
	slices.Reverse(names)
	completed := make(map[string]bool)
	for _, name := range names {
		if strings.HasSuffix(name, ".sha256") {
			completed[strings.TrimSuffix(name, ".sha256")] = true
		}
	}

	count := 1
	for _, name := range names {
		if !archiveName.MatchString(name) || name == latest || !completed[name] {
			continue
		}
		count++
		if count <= keep {
			continue
		}
		err = cloud(ctx, env, nil, io.Discard, "deletefile", remote+"/"+name)
		if err != nil {
			return err
		}
		err = cloud(ctx, env, nil, io.Discard, "deletefile", remote+"/"+name+".sha256")
		if err != nil {
			return err
		}
	}
	return nil
}
