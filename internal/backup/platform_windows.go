package backup

import (
	"fmt"
	"golang.org/x/sys/windows"
	"os"
	"path/filepath"
)

func lockState(dir string) (*os.File, error) {
	file, err := os.OpenFile(filepath.Join(dir, "run.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	var overlapped windows.Overlapped
	err = windows.LockFileEx(windows.Handle(file.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &overlapped)
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
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}
	var free, total, available uint64
	err = windows.GetDiskFreeSpaceEx(name, &free, &total, &available)
	return int64(free), err
}
