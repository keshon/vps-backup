//go:build linux || darwin

package backup

import (
	"fmt"
	"golang.org/x/sys/unix"
	"os"
	"path/filepath"
)

func lockState(dir string) (*os.File, error) {
	file, err := os.OpenFile(filepath.Join(dir, "run.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	err = unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB)
	if err != nil {
		closeErr := file.Close()
		if closeErr != nil {
			return nil, closeErr
		}
		return nil, fmt.Errorf("another backup is running; wait for it to finish")
	}
	return file, nil
}

func freeSpace(path string) (int64, error) {
	var stat unix.Statfs_t
	err := unix.Statfs(path, &stat)
	return int64(stat.Bavail) * int64(stat.Bsize), err
}
