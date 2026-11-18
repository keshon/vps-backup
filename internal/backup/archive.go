package backup

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func archive(ctx context.Context, app App, output io.Writer) (string, int64, error) {
	root, err := os.OpenRoot(app.Path)
	if err != nil {
		return "", 0, err
	}
	defer root.Close()

	hash := sha256.New()
	w := tar.NewWriter(io.MultiWriter(output, hash))
	var size int64
	err = filepath.WalkDir(app.Path, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}

		rel, err := filepath.Rel(app.Path, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		for _, pattern := range app.Excludes {
			pattern = strings.TrimPrefix(pattern, "./")
			matched, matchErr := filepath.Match(pattern, filepath.ToSlash(rel))
			if matchErr != nil {
				return matchErr
			}
			if !strings.Contains(pattern, "/") {
				matched, matchErr = filepath.Match(pattern, entry.Name())
				if matchErr != nil {
					return matchErr
				}
			}
			if matched {
				if entry.IsDir() {
					return filepath.SkipDir
				}
				return nil
			}
		}

		info, err := entry.Info()
		if err != nil {
			return err
		}
		link := ""
		if info.Mode()&os.ModeSymlink != 0 {
			link, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}
		if !info.Mode().IsRegular() && !info.IsDir() && link == "" {
			return fmt.Errorf("%s: unsupported special file; exclude it before backing up", path)
		}

		header, err := tar.FileInfoHeader(info, link)
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)
		header.AccessTime, header.ChangeTime = time.Time{}, time.Time{}
		header.Uname, header.Gname = "", ""
		err = w.WriteHeader(header)
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return nil
		}

		file, err := root.Open(rel)
		if err != nil {
			return err
		}
		n, err := io.Copy(w, contextReader{ctx: ctx, input: file})
		closeErr := file.Close()
		size += n
		if err != nil {
			return err
		}
		return closeErr
	})

	closeErr := w.Close()
	if err != nil {
		return "", size, err
	}
	if closeErr != nil {
		return "", size, closeErr
	}
	return hex.EncodeToString(hash.Sum(nil)), size, nil
}

type contextReader struct {
	ctx   context.Context
	input io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if r.ctx.Err() != nil {
		return 0, r.ctx.Err()
	}
	return r.input.Read(p)
}

func compressedArchive(ctx context.Context, app App, path string) (string, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return "", err
	}
	gz := gzip.NewWriter(file)
	fingerprint, _, archiveErr := archive(ctx, app, gz)
	gzipErr := gz.Close()
	closeErr := file.Close()
	if archiveErr != nil {
		return "", archiveErr
	}
	if gzipErr != nil {
		return "", gzipErr
	}
	if closeErr != nil {
		return "", closeErr
	}
	return fingerprint, nil
}
