package tui

import (
	"path/filepath"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"
	"github.com/keshon/vps-backup/internal/backup"
	"github.com/keshon/vps-backup/internal/tui/components/popup"
)

func fixture(t *testing.T) *Model {
	t.Helper()
	cfg := backup.Config{ServerID: "sample", Remote: "local:backups", StateDir: t.TempDir(), Keep: 3, ReserveMiB: 1}
	return New(cfg, filepath.Join(t.TempDir(), "config.json"))
}

func TestMouseSavesFormAndCancelsConfirmation(t *testing.T) {
	for _, width := range []int{42, 100} {
		m := fixture(t)
		m.width = width
		m.openSettings()
		m.form.Editor.SetValue("clicked-server")
		m.View()
		rows, hits := m.form.Rows(m.formProps())
		for _, hit := range hits {
			if hit.Index != len(m.form.Fields) {
				continue
			}
			x := max((m.width-popup.ModalWidth(m.width))/2, 0) + 2 + hit.X0
			y := max((m.height-len(rows)-2)/2, 0) + 1 + hit.Y0
			m.Update(tea.MouseClickMsg{X: x, Y: y, Button: tea.MouseLeft})
			break
		}
		if m.form != nil || m.cfg.ServerID != "clicked-server" {
			t.Fatalf("mouse save failed at %d: %s", width, m.notice)
		}
		m.confirm = "backup"
		rows = m.confirmRows()
		x := max((m.width-popup.ModalWidth(m.width))/2, 0) + 2 + strings.Index(rows[len(rows)-1], "[ Cancel ]")
		y := max((m.height-len(rows)-2)/2, 0) + len(rows)
		m.Update(tea.MouseClickMsg{X: x, Y: y, Button: tea.MouseLeft})
		if m.confirm != "" || m.busy {
			t.Fatal("mouse cancel activated backup")
		}
		m.Close()
	}
}

func key(code rune) tea.KeyPressMsg { return tea.KeyPressMsg{Code: code, Text: string(code)} }

func TestSettingsUseReusedFormAndSave(t *testing.T) {
	m := fixture(t)
	defer m.Close()
	m.Update(key('s'))
	if m.form == nil || !m.settingsForm {
		t.Fatal("settings action did not open the reusable form")
	}
	m.form.Editor.SetValue("renamed")
	m.applyForm()
	if m.form != nil {
		t.Fatal(m.notice)
	}
	cfg, err := backup.Load(m.path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ServerID != "renamed" {
		t.Fatal(cfg.ServerID)
	}
	m.openApp(backup.App{ID: "settings", Path: t.TempDir()})
	m.applyForm()
	if len(m.cfg.Apps) != 1 || m.cfg.Apps[0].ID != "settings" {
		t.Fatal("app name collided with settings navigation:", m.notice)
	}
}

func TestNarrowViewsAndCredentialMasking(t *testing.T) {
	m := fixture(t)
	defer m.Close()
	for _, size := range [][2]int{{20, 8}, {40, 18}, {100, 32}} {
		m.Update(tea.WindowSizeMsg{Width: size[0], Height: size[1]})
		view := m.View()
		for _, line := range strings.Split(view.Content, "\n") {
			if ansi.StringWidth(line) > size[0] {
				t.Fatal("view exceeded terminal width")
			}
		}
	}
	m.secretOpen = true
	m.secret.SetValue("private-config-data")
	view := m.View()
	if strings.Contains(view.Content, "private-config-data") {
		t.Fatal("secret visible")
	}
}

func TestInputsHaveNoPromptRailAndContainerHint(t *testing.T) {
	m := fixture(t)
	defer m.Close()
	m.openApp(backup.App{})
	m.View()
	if m.form.Editor.Prompt != "" || m.form.Editor.Height() != 1 {
		t.Fatal("single-line input retained textarea rail or extra rows")
	}
	rows, hits := m.form.Rows(m.formProps())
	for _, hit := range hits {
		if hit.Index != 2 {
			continue
		}
		x := max((m.width-popup.ModalWidth(m.width))/2, 0) + 3
		y := max((m.height-len(rows)-2)/2, 0) + 1 + hit.Y0
		m.Update(tea.MouseClickMsg{X: x, Y: y, Button: tea.MouseLeft})
		break
	}
	props := m.formProps()
	if m.form.Index != 2 || m.form.Editor.Height() != 3 || !strings.Contains(props.Hint, "restart before upload") {
		t.Fatal("container input click or explanation is missing:", m.form.Index, props.Hint)
	}
}
