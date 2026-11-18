package tui

import (
	"encoding/binary"
	"strings"
	"testing"

	xwindows "github.com/charmbracelet/x/windows"
	"golang.org/x/sys/windows"
)

func TestCopiedNativeConsoleKeepsMouseInput(t *testing.T) {
	mode := nativeConsoleMode(windows.ENABLE_QUICK_EDIT_MODE | windows.ENABLE_LINE_INPUT)
	if mode&windows.ENABLE_QUICK_EDIT_MODE != 0 || mode&windows.ENABLE_MOUSE_INPUT == 0 {
		t.Fatal("native mouse input was not enabled")
	}
	reader := &consoleReader{}
	record := xwindows.InputRecord{EventType: xwindows.MOUSE_EVENT}
	binary.LittleEndian.PutUint16(record.Event[0:2], 5)
	binary.LittleEndian.PutUint16(record.Event[2:4], 7)
	binary.LittleEndian.PutUint32(record.Event[4:8], xwindows.FROM_LEFT_1ST_BUTTON_PRESSED)
	reader.encode(record)
	if !strings.Contains(reader.buffer.String(), "\x1b[<0;6;8M") {
		t.Fatalf("native mouse click not encoded: %q", reader.buffer.String())
	}
}
