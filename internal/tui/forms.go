package tui

import (
	"encoding/base64"
	"slices"
	"strconv"
	"strings"

	"charm.land/bubbles/v2/textarea"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/keshon/vps-backup/internal/backup"
	"github.com/keshon/vps-backup/internal/tui/components/form"
)

func (m *Model) openFields(title string, fields []form.Field) {
	editor := textarea.New()
	editor.SetHeight(1)
	editor.Prompt = ""
	editor.ShowLineNumbers = false
	editor.EndOfBufferCharacter = ' '
	editor.CharLimit = 8192
	fStyles, _ := styles()
	inputStyles := textarea.DefaultStyles(true)
	state := textarea.StyleState{Base: fStyles.Normal, Text: fStyles.Normal, CursorLine: fStyles.Normal, EndOfBuffer: fStyles.Normal, Placeholder: fStyles.Muted, Prompt: fStyles.Focus, Selection: fStyles.Focus.Reverse(true)}
	inputStyles.Focused = state
	inputStyles.Blurred = state
	inputStyles.Cursor.Color = lipgloss.Color("#A5B4FC")
	editor.SetStyles(inputStyles)
	f := form.New(fields, editor)
	f.Focus(0, nil)
	m.form = &f
	m.formTitle = title
	m.notice = ""
}

func (m *Model) openSettings() {
	m.settingsForm = true
	m.openFields("Backup settings", []form.Field{
		{Key: "server", Label: "Server name", Value: m.cfg.ServerID},
		{Key: "remote", Label: "Cloud destination (remote:folder)", Value: m.cfg.Remote},
		{Key: "state", Label: "Working folder", Value: m.cfg.StateDir},
		{Key: "keep", Label: "Archives to keep per app", Value: strconv.Itoa(m.cfg.Keep)},
		{Key: "reserve", Label: "Free space reserve (MiB)", Value: strconv.FormatInt(m.cfg.ReserveMiB, 10)},
	})
}

func (m *Model) openApp(app backup.App) {
	m.settingsForm = false
	m.editing = app.ID
	m.openFields("App folders", []form.Field{
		{Key: "id", Label: "App name", Value: app.ID},
		{Key: "path", Label: "Absolute source folder", Value: app.Path},
		{Key: "stop", Label: "Containers to stop (optional; one per line)", Value: strings.Join(app.StopContainers, "\n"), Multiline: true},
		{Key: "exclude", Label: "Exclude patterns (one per line)", Value: strings.Join(app.Excludes, "\n"), Multiline: true},
	})
}

func lines(value string) []string {
	var out []string
	for _, line := range strings.Split(value, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}

func (m *Model) applyForm() {
	values := m.form.Values()
	cfg := m.cfg
	cfg.Apps = slices.Clone(m.cfg.Apps)

	if m.settingsForm {
		keep, err := strconv.Atoi(values["keep"])
		if err != nil {
			m.notice = "Enter a whole number of archives to keep."
			return
		}
		reserve, err := strconv.ParseInt(values["reserve"], 10, 64)
		if err != nil {
			m.notice = "Enter a whole number of MiB for the reserve."
			return
		}
		cfg.ServerID, cfg.Remote, cfg.StateDir, cfg.Keep, cfg.ReserveMiB = values["server"], values["remote"], values["state"], keep, reserve
	} else {
		app := backup.App{ID: values["id"], Path: values["path"], StopContainers: lines(values["stop"]), Excludes: lines(values["exclude"])}
		replaced := false
		for i := range cfg.Apps {
			if cfg.Apps[i].ID == m.editing {
				cfg.Apps[i] = app
				replaced = true
				break
			}
		}
		if !replaced {
			cfg.Apps = append(cfg.Apps, app)
		}
	}

	err := backup.Save(m.path, cfg)
	if err != nil {
		m.notice = err.Error()
		return
	}

	m.cfg = cfg
	m.form = nil
	m.refresh()
	m.notice = "Settings saved to " + m.path
}

func (m *Model) removeApp() {
	cfg := m.cfg
	cfg.Apps = slices.DeleteFunc(slices.Clone(cfg.Apps), func(app backup.App) bool { return app.ID == m.table.Selected() })

	err := backup.Save(m.path, cfg)
	if err != nil {
		m.notice = err.Error()
		return
	}

	m.cfg = cfg
	m.refresh()
	m.notice = "App removed from this configuration. Cloud archives were kept."
}

func (m *Model) updateSecret(msg tea.Msg) (tea.Model, tea.Cmd) {
	if key, ok := msg.(tea.KeyPressMsg); ok {
		switch key.String() {
		case "esc":
			m.secretOpen = false
			m.secret.SetValue("")
			return m, nil
		case "enter":
			value := strings.TrimSpace(m.secret.Value())
			_, err := base64.StdEncoding.DecodeString(value)
			if err != nil || value == "" {
				m.notice = "Paste base64-encoded rclone configuration; this value is not valid base64."
				return m, nil
			}
			m.credentials = value
			m.secret.SetValue("")
			m.secretOpen = false
			m.notice = "Cloud credentials ready for this session. They were not saved."
			return m, nil
		}
	}
	var cmd tea.Cmd
	m.secret, cmd = m.secret.Update(msg)
	return m, cmd
}
