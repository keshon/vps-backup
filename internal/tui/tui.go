package tui

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/keshon/vps-backup/internal/backup"
	"github.com/keshon/vps-backup/internal/tui/components/cell"
	"github.com/keshon/vps-backup/internal/tui/components/form"
	"github.com/keshon/vps-backup/internal/tui/components/pane"
	"github.com/keshon/vps-backup/internal/tui/components/popup"
	"github.com/keshon/vps-backup/internal/tui/components/table"
)

type Model struct {
	cfg           backup.Config
	path          string
	width, height int
	table         *table.Model
	form          *form.Model
	formTitle     string
	editing       string
	settingsForm  bool
	secret        textinput.Model
	secretOpen    bool
	credentials   string
	confirm       string
	notice        string
	activity      bool
	logs          []string
	logOffset     int
	busy          bool
	cancel        context.CancelFunc
	done          <-chan struct{}
	progress      <-chan string
	completed     <-chan completed
	quitWhenDone  bool
}

func New(cfg backup.Config, path string) *Model {
	input := textinput.New()
	input.EchoMode = textinput.EchoPassword
	input.CharLimit = 1024 * 1024
	input.Placeholder = "Paste base64 rclone configuration"
	m := &Model{cfg: cfg, path: path, width: 100, height: 32, table: table.New(textinput.DefaultStyles(true)), secret: input, credentials: os.Getenv("RCLONE_CONFIG_CONTENT")}
	m.notice = "S settings  •  A add app  •  R backup  •  H activity  •  Q quit"
	if cfg.Remote == "" {
		m.notice = "Start with Settings to choose your server name and cloud destination."
	}
	m.refresh()
	report, err := backup.LastReport(cfg.StateDir)
	if err == nil {
		m.logs = append(m.logs, report.Finished.Local().Format("2006-01-02 15:04")+" — "+report.Status)
		m.appendReport(report)
	}
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		m.logs = append(m.logs, "Could not read previous result: "+err.Error())
	}
	return m
}

func Run(ctx context.Context, cfg backup.Config, path string) error {
	m := New(cfg, path)
	defer m.Close()
	options, restore, err := consoleInput(ctx)
	if err != nil {
		return err
	}
	defer restore()
	options = append(options, tea.WithContext(ctx))
	_, err = tea.NewProgram(m, options...).Run()
	return err
}

func (m *Model) Close() {
	if m.cancel != nil {
		m.cancel()
	}
	if m.done != nil {
		<-m.done
	}
	m.credentials = ""
}

func (m *Model) Init() tea.Cmd { return nil }

func (m *Model) refresh() {
	rows := make([]table.Row, 0, len(m.cfg.Apps))
	for _, app := range m.cfg.Apps {
		consistency := "live files"
		if len(app.StopContainers) > 0 {
			consistency = "stop " + strings.Join(app.StopContainers, ", ")
		}
		rows = append(rows, table.Row{ID: app.ID, Cells: []string{app.ID, app.Path, consistency}})
	}
	m.table.SetRows(rows)
}

func styles() (form.Styles, popup.Styles) {
	normal := lipgloss.NewStyle().Foreground(lipgloss.Color("#E5E7EF")).Background(lipgloss.Color("#171923"))
	focus := normal.Foreground(lipgloss.Color("#A5B4FC"))
	f := form.Styles{Normal: normal, Title: focus.Bold(true), Focus: focus, Muted: normal.Foreground(lipgloss.Color("#9AA2B4")), Disabled: normal.Foreground(lipgloss.Color("#606879")), Error: normal.Foreground(lipgloss.Color("#FDA4AF")), Scrollbar: focus}
	return f, popup.Styles{Normal: normal, Border: focus, Selected: focus.Bold(true)}
}

func (m *Model) tableProps() table.Props {
	f, s := styles()
	return table.Props{
		Title: "VPS Backup / Apps", Description: m.cfg.ServerID + "  →  " + m.cfg.Remote + "\n" + m.notice,
		EmptyMessage: "No apps yet. Choose Settings, then Add app.", DefaultAction: "edit", Width: m.width, Height: m.height,
		Columns: []table.Column{{Label: "App", Width: 18}, {Label: "Source"}, {Label: "Consistency", Width: 24, HideNarrow: true}},
		Actions: []table.Action{
			{ID: "add", Label: "Add app", Key: "a", Enabled: true},
			{ID: "edit", Label: "Edit", Key: "e", Enabled: len(m.cfg.Apps) > 0},
			{ID: "remove", Label: "Remove", Key: "d", Enabled: len(m.cfg.Apps) > 0, Destructive: true},
			{ID: "settings", Label: "Settings", Key: "s", Enabled: true},
			{ID: "credentials", Label: "Credentials", Key: "c", Enabled: true},
			{ID: "backup", Label: "Back up", Key: "r", Enabled: len(m.cfg.Apps) > 0},
			{ID: "history", Label: "Activity", Key: "h", Enabled: true},
			{ID: "quit", Label: "Quit", Key: "q", Enabled: true},
		}, Styles: f, Surface: s,
	}
}

func (m *Model) View() tea.View {
	f, s := styles()
	var content string
	switch {
	case m.width < 32 || m.height < 12:
		content = "Resize the terminal to at least 32 columns and 12 rows. Q quits."
	case m.secretOpen:
		m.secret.SetWidth(max(popup.ModalWidth(m.width)-8, 1))
		content = popup.FrameBox(m.secretRows(), m.width, m.height, s)
	case m.confirm != "":
		content = popup.FrameBox(m.confirmRows(), m.width, m.height, s)
	case m.form != nil:
		m.form.Editor.SetWidth(max(popup.ModalWidth(m.width)-8, 1))
		rows, _ := m.form.Rows(m.formProps())
		content = popup.FrameBox(rows, m.width, m.height, s)
	case m.activity:
		layout := pane.Measure(pane.Props{Width: m.width, Height: m.height, Outlined: true, BottomEdge: true, FooterHeight: 2})
		end := max(len(m.logs)-m.logOffset, 0)
		start := max(end-layout.Body.Height, 0)
		lines := m.logs[start:end]
		body := strings.Join(lines, "\n")
		if body == "" {
			body = "No activity in this session. Start a backup from the Apps screen."
		}
		footer := []string{cell.Line(m.notice, layout.Footer.Width), m.activityButtons()}
		rows := pane.Render(layout, "VPS Backup / Activity", body, footer, pane.Styles{Border: s.Border, Title: f.Title, Focus: f.Focus}, true)
		content = strings.Join(rows, "\n")
	default:
		layout := m.table.Layout(m.tableProps())
		content = popup.FrameBox(layout.Rows, m.width, m.height, s)
	}
	v := tea.NewView(cell.Frame(content, m.width, m.height))
	v.BackgroundColor = lipgloss.Color("#171923")
	v.ForegroundColor = lipgloss.Color("#E5E7EF")
	v.WindowTitle = "VPS Backup"
	v.AltScreen = true
	v.MouseMode = tea.MouseModeCellMotion
	return v
}

func ConfigPath() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("finding settings folder: %w", err)
	}
	return filepath.Join(dir, "vps-backup", "config.json"), nil
}
