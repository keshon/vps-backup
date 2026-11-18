# Gektor component reuse

Copied from `gektor/internal/tui/components` at commit
`9622fe3c8f8e4060a9df1232830b0be01fd190f8`:

| Component | Used here for |
| --- | --- |
| table | App rows, filtering, selection, keyboard actions and mouse actions |
| form | Backup settings and app editors, validation feedback, Save/Cancel |
| popup | Forms, temporary credentials and confirmation surfaces |
| pane | Activity layout and its footer regions |
| cell | Terminal-width-safe fitting and wrapping |
| selection | Table cursor movement and scrolling |

The copied implementation and tests only change module import paths. Styles,
backup state, persistence and operation ownership stay in the parent TUI. No
component imports the backup engine. This is a local reuse proof, not a published
toolkit or a dependency on the Gektor checkout. Gektor itself is unchanged.

The parent also copies Gektor's `console_windows.go` and `console_other.go` input
adapters to retain native Windows mouse and paste behavior. The application
routes form hit regions and confirmation clicks using the renderer's geometry.

The original MIT notice is preserved in [LICENSE.gektor](LICENSE.gektor).
