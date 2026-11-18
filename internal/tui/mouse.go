package tui

import (
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/keshon/vps-backup/internal/tui/components/cell"
	"github.com/keshon/vps-backup/internal/tui/components/form"
	"github.com/keshon/vps-backup/internal/tui/components/pane"
	"github.com/keshon/vps-backup/internal/tui/components/popup"
)

func (m *Model) formProps() form.Props {
	f, _ := styles()
	height := 1
	hint := ""
	if m.form.Index < len(m.form.Fields) {
		field := m.form.Fields[m.form.Index]
		if field.Multiline {
			height = 3
		}
		if field.Key == "stop" {
			hint = "List containers that write data in this folder. They stop during the local copy and restart before upload. Leave empty for static files. Find names with: docker ps --format '{{.Names}}'"
		}
		if field.Key == "exclude" {
			hint = "Paths relative to this folder, or filename patterns such as *.log. One pattern per line. Matching folders are skipped with their contents."
		}
	}
	m.form.Editor.SetHeight(height)
	return form.Props{Title: m.formTitle, Width: m.width, Height: m.height, PrimaryLabel: "Save", Hint: hint, Error: m.notice, Styles: f}
}

func (m *Model) activityButtons() string {
	if m.busy {
		return "[ Cancel backup ]  [ Quit ]"
	}
	return "[ Apps ]  [ Quit ]"
}

func (m *Model) activityMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	mouse := msg.Mouse()
	switch mouse.Button {
	case tea.MouseWheelUp:
		m.logOffset = min(m.logOffset+3, max(len(m.logs)-1, 0))
		return m, nil
	case tea.MouseWheelDown:
		m.logOffset = max(m.logOffset-3, 0)
		return m, nil
	}
	_, clicked := msg.(tea.MouseClickMsg)
	if !clicked || mouse.Button != tea.MouseLeft {
		return m, nil
	}
	layout := pane.Measure(pane.Props{Width: m.width, Height: m.height, Outlined: true, BottomEdge: true, FooterHeight: 2})
	if mouse.Y != layout.Footer.Y+1 {
		return m, nil
	}
	x := mouse.X - layout.Footer.X
	buttons := m.activityButtons()
	quit := strings.Index(buttons, "[ Quit ]")
	if x >= quit && x < quit+len("[ Quit ]") {
		if !m.busy {
			return m, tea.Quit
		}
		m.quitWhenDone = true
		m.cancel()
		m.notice = "Cancelling; waiting for cleanup and container restarts."
		return m, nil
	}
	if x >= 0 && x < strings.Index(buttons, "  ") {
		if !m.busy {
			m.activity = false
			return m, nil
		}
		m.cancel()
		m.notice = "Cancelling; waiting for cleanup and container restarts."
	}
	return m, nil
}

func (m *Model) confirmRows() []string {
	text := "Create verified archives for all selected apps?"
	if m.confirm == "remove" {
		text = "Remove " + m.table.Selected() + " from this configuration? Cloud archives will stay."
	}
	rows := []string{"Confirm"}
	rows = append(rows, cell.Wrap(text, max(popup.ModalWidth(m.width)-4, 1))...)
	return append(rows, "", "[ Confirm ]  [ Cancel ]")
}

func (m *Model) secretRows() []string {
	return []string{"Cloud credentials", "Paste base64 rclone config. Kept in memory only.", "", m.secret.View(), "", m.notice, "[ Use ]  [ Cancel ]"}
}

// Coordinates use the same frame geometry as Gektor's form and popup renderers.
func (m *Model) modalPoint(mouse tea.Mouse, rows []string) (int, int) {
	width := popup.ModalWidth(m.width)
	left := max((m.width-width)/2, 0) + 2
	top := max((m.height-len(rows)-2)/2, 0) + 1
	return mouse.X - left, mouse.Y - top
}

func (m *Model) formMouse(msg tea.MouseMsg) {
	rows, hits := m.form.Rows(m.formProps())
	mouse := msg.Mouse()
	x, y := m.modalPoint(mouse, rows)
	if x < 0 || x >= popup.ModalWidth(m.width)-4 || y < 0 || y >= len(rows) {
		return
	}
	switch mouse.Button {
	case tea.MouseWheelUp:
		m.form.Focus(m.form.Index-1, nil)
		return
	case tea.MouseWheelDown:
		m.form.Focus(m.form.Index+1, nil)
		return
	}
	_, clicked := msg.(tea.MouseClickMsg)
	if !clicked || mouse.Button != tea.MouseLeft {
		return
	}
	for _, hit := range hits {
		if y < hit.Y0 || y >= hit.Y1 {
			continue
		}
		if hit.X1 > hit.X0 && (x < hit.X0 || x >= hit.X1) {
			continue
		}
		if hit.Index == len(m.form.Fields) {
			m.applyForm()
			return
		}
		if hit.Index == len(m.form.Fields)+1 {
			m.form = nil
			m.notice = "Changes discarded."
			return
		}
		m.form.Focus(hit.Index, nil)
		return
	}
}

func (m *Model) modalButton(msg tea.MouseMsg, rows []string, primary string) string {
	mouse := msg.Mouse()
	_, clicked := msg.(tea.MouseClickMsg)
	if !clicked || mouse.Button != tea.MouseLeft {
		return ""
	}
	x, y := m.modalPoint(mouse, rows)
	if y != len(rows)-1 || x < 0 {
		return ""
	}
	primaryWidth := cell.Width(primary)
	if x < primaryWidth {
		return "enter"
	}
	cancel := strings.Index(rows[len(rows)-1], "[ Cancel ]")
	if x >= cancel && x < cancel+len("[ Cancel ]") {
		return "esc"
	}
	return ""
}
