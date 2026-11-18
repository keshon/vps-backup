package tui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/keshon/vps-backup/internal/backup"
	"github.com/keshon/vps-backup/internal/tui/components/form"
)

type completed struct {
	report backup.Report
	err    error
}

func (m *Model) start() tea.Cmd {
	ctx, cancel := context.WithCancel(context.Background())
	progress := make(chan string, 32)
	finished := make(chan completed, 1)
	done := make(chan struct{})
	m.cancel, m.done, m.progress, m.completed = cancel, done, progress, finished
	m.busy, m.activity = true, true
	m.notice = "Backup running. C cancels safely; containers restart before completion."
	cfg, credentials := m.cfg, m.credentials
	go func() {
		defer close(done)
		report, err := backup.Run(ctx, cfg, credentials, func(line string) {
			select {
			case progress <- line:
			case <-ctx.Done():
			}
		})
		finished <- completed{report: report, err: err}
	}()
	return m.wait()
}

func (m *Model) wait() tea.Cmd {
	progress, finished := m.progress, m.completed
	return func() tea.Msg {
		select {
		case line := <-progress:
			return line
		case result := <-finished:
			return result
		}
	}
}

func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch event := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = event.Width, event.Height
		return m, nil
	case string:
		m.logs = append(m.logs, event)
		if len(m.logs) > 500 {
			m.logs = m.logs[len(m.logs)-500:]
		}
		return m, m.wait()
	case completed:
		m.busy = false
		m.cancel()
		m.appendReport(event.report)
		m.notice = "Backup complete. Uploaded archives passed SHA-256 readback."
		if event.err != nil {
			m.notice = event.err.Error()
		}
		m.logs = append(m.logs, m.notice)
		if m.quitWhenDone {
			return m, tea.Quit
		}
		return m, nil
	case tea.KeyPressMsg:
		if event.String() == "ctrl+c" {
			if m.busy {
				m.quitWhenDone = true
				m.cancel()
				m.notice = "Cancelling; waiting for cleanup and container restarts."
				return m, nil
			}
			return m, tea.Quit
		}
	}
	if m.secretOpen {
		if mouse, ok := msg.(tea.MouseMsg); ok {
			action := m.modalButton(mouse, m.secretRows(), "[ Use ]")
			if action == "enter" {
				return m.updateSecret(tea.KeyPressMsg{Code: tea.KeyEnter})
			}
			if action == "esc" {
				return m.updateSecret(tea.KeyPressMsg{Code: tea.KeyEscape})
			}
			return m, nil
		}
		return m.updateSecret(msg)
	}
	if m.confirm != "" {
		if mouse, ok := msg.(tea.MouseMsg); ok {
			action := m.modalButton(mouse, m.confirmRows(), "[ Confirm ]")
			if action == "enter" {
				msg = tea.KeyPressMsg{Code: tea.KeyEnter}
			}
			if action == "esc" {
				msg = tea.KeyPressMsg{Code: tea.KeyEscape}
			}
		}
		key, ok := msg.(tea.KeyPressMsg)
		if !ok {
			return m, nil
		}
		if key.String() == "esc" {
			m.confirm = ""
		}
		if key.String() == "enter" {
			operation := m.confirm
			m.confirm = ""
			if operation == "remove" {
				m.removeApp()
				return m, nil
			}
			return m, m.start()
		}
		return m, nil
	}
	if m.form != nil {
		if mouse, ok := msg.(tea.MouseMsg); ok {
			m.formMouse(mouse)
			return m, nil
		}
		intent, cmd := m.form.Update(msg, nil, false, false)
		if intent == form.Cancel {
			m.form = nil
			m.notice = "Changes discarded."
		}
		if intent == form.Apply {
			m.applyForm()
		}
		return m, cmd
	}
	if m.activity {
		if mouse, ok := msg.(tea.MouseMsg); ok {
			return m.activityMouse(mouse)
		}
		key, ok := msg.(tea.KeyPressMsg)
		if !ok {
			return m, nil
		}
		switch key.String() {
		case "up", "pgup":
			m.logOffset = min(m.logOffset+3, max(len(m.logs)-1, 0))
		case "down", "pgdown":
			m.logOffset = max(m.logOffset-3, 0)
		case "c":
			if m.busy {
				m.cancel()
				m.notice = "Cancelling; waiting for cleanup."
			}
		case "esc":
			if !m.busy {
				m.activity = false
			}
		case "q":
			if m.busy {
				m.quitWhenDone = true
				m.cancel()
				m.notice = "Cancelling; waiting for cleanup."
			} else {
				return m, tea.Quit
			}
		}
		return m, nil
	}
	var action string
	var cmd tea.Cmd
	if mouse, ok := msg.(tea.MouseMsg); ok {
		action = m.table.Mouse(mouse, m.tableProps())
	} else {
		action, cmd = m.table.Update(msg, m.tableProps())
	}
	switch action {
	case "add":
		m.openApp(backup.App{})
	case "edit":
		for _, app := range m.cfg.Apps {
			if app.ID == m.table.Selected() {
				m.openApp(app)
				break
			}
		}
	case "remove":
		m.confirm = "remove"
	case "settings":
		m.openSettings()
	case "credentials":
		m.secretOpen = true
		m.secret.SetValue("")
		m.secret.Focus()
		m.notice = "Credentials are never saved in settings."
	case "backup":
		err := m.cfg.Validate()
		if err != nil {
			m.notice = err.Error()
			break
		}
		if strings.TrimSpace(m.credentials) == "" {
			m.secretOpen = true
			m.secret.Focus()
			m.notice = "Supply cloud credentials, then choose Back up again."
			break
		}
		m.confirm = "backup"
	case "history":
		m.activity = true
	case "quit", "close":
		return m, tea.Quit
	}
	return m, cmd
}

func (m *Model) appendReport(report backup.Report) {
	for _, app := range report.Apps {
		state := fmt.Sprintf("saved %d bytes", app.Bytes)
		if app.Unchanged {
			state = "unchanged"
		}
		m.logs = append(m.logs, app.ID+": "+state+" — "+app.Archive)
	}
	if report.Error != "" {
		m.logs = append(m.logs, report.Error)
	}
}
