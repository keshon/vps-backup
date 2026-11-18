# n8n

The workflow runs the Go binary over SSH. No TUI opens during scheduled runs.

## Set up

1. On the target VPS, build the Linux binary as described in the README.
   Put it at `/opt/scripts/vps-backup/vps-backup`.
2. Run the TUI as the same user n8n will connect as. Save Settings and apps.
   For root, the default settings file is `/root/.config/vps-backup/config.json`.
3. Import [n8n/workflow.json](../n8n/workflow.json) into n8n.
4. In **Settings**, fill `rcloneConfigBase64`. Encode your existing rclone INI
   on the machine where it is stored:

   ```sh
   base64 -w0 /path/to/rclone.conf
   ```

   On Windows PowerShell:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\path\to\rclone.conf'))
   ```

5. In **Run backup**, select your target VPS SSH credential. Adjust the binary
   and config paths if you chose different locations.
6. Run **Manual backup**. Open the SSH node output for progress and the JSON
   result. A nonzero exit or unsuccessful report stops the workflow.
7. Enable the notifications you want, then publish the workflow.

The template contains no credentials, starts unpublished, and runs weekly on
Wednesday at 05:00 Moscow time once published. Edit the schedule node to change it.
The execution timeout is one hour; increase it in workflow settings for larger backups.

## Notifications

Both output nodes start disabled. Enable either or both independently:

- **Discord**: select a webhook credential.
- **Telegram**: select a bot credential and enter your chat ID.

Messages show the server, uploaded bytes, saved/unchanged apps and archive names.
Unchanged apps contribute zero uploaded bytes. Cloud capacity is not included.
Notification delivery errors leave the completed backup result intact; inspect
notification node output if a message is missing. Backup failures stop before
notifications; configure an n8n error workflow if you want failure alerts.

## Credentials and scheduling

Base64 is not encryption. Keep the filled workflow private: n8n can retain the
Settings value and resolved SSH command in execution history. The target receives
credentials for that run; the app does not write an rclone config there.

Do not configure a workflow to stop the n8n container it is running inside.
Use a separate n8n instance when backing up that container.

For another VPS, duplicate the workflow, select its SSH credential and save its
apps in its own TUI. Give each VPS a unique server name.

The underlying command is:

```sh
RCLONE_CONFIG_CONTENT='BASE64_RCLONE_INI' \
  /opt/scripts/vps-backup/vps-backup --run \
  --config /root/.config/vps-backup/config.json
```

JSON goes to stdout, progress to stderr, and failures exit nonzero.
