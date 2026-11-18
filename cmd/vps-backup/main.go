package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/keshon/vps-backup/internal/backup"
	"github.com/keshon/vps-backup/internal/tui"
)

func main() {
	err := run()
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run() error {
	path, err := tui.ConfigPath()
	if err != nil {
		return err
	}
	flags := flag.NewFlagSet("vps-backup", flag.ContinueOnError)
	config := flags.String("config", path, "settings file (created when you save in the TUI)")
	headless := flags.Bool("run", false, "back up configured apps without opening the TUI")
	err = flags.Parse(os.Args[1:])
	if err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("unexpected argument %q; run without arguments to open the TUI", flags.Arg(0))
	}
	cfg, err := backup.Load(*config)
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if !*headless {
		return tui.Run(ctx, cfg, *config)
	}
	report, runErr := backup.Run(ctx, cfg, os.Getenv("RCLONE_CONFIG_CONTENT"), func(line string) { fmt.Fprintln(os.Stderr, line) })
	if runErr != nil {
		report.Status = "failed"
		report.Error = runErr.Error()
	}
	err = json.NewEncoder(os.Stdout).Encode(report)
	if err != nil {
		return err
	}
	return runErr
}
