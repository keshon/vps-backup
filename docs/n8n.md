# Schedule with n8n

Both storage modes use the same workflow. The VPS's configuration chooses the
format; n8n only connects through SSH and runs the client.

1. Install and test the VPS client using [setup](setup.md).
2. Import [deploy/n8n-backup.json](../deploy/n8n-backup.json) into n8n.
3. In **Run backup**, select your SSH credential. The template contains no
   credentials. Use root, or an account permitted to run the command with `sudo -n`.
4. In **Read cloud space**, select the backup server's SSH credential. This may
   be a different machine from **Run backup**. It needs access to the private
   server configuration; cloud credentials stay there.
5. Configure any notifications you want, then review the schedule and publish.

The example runs Wednesdays at 05:00 Moscow time. Its SSH command is:

```bash
/usr/local/bin/vps-backup --config /etc/vps-backup/client.json backup --json --min-age-hours 20
```

The validation node requires SSH exit code 0 and JSON `status: "success"`.
Skipping a recent successful backup counts as success. Backup failures fail the workflow;
completion notifications only follow a successful result. The manual trigger uses the same recent-success
guard. To force another backup, run `sudo vps-backup backup --force` on the VPS.

For the commands to disable the local timer or add a systemd fallback, see
[the scheduling examples](setup.md#n8n-only). Changing the workflow does not change
the VPS's timer. Set the workflow timeout long enough for your backup to finish.

## Discord and Telegram

Both notification nodes are connected as separate branches and disabled in the
template. No credential references, webhook URLs, bot tokens or chat IDs are
included. Configure only the channels you want:

- **Discord:** select your Discord webhook credential, then enable the node.
- **Telegram:** select a Telegram bot credential, fill in **Chat ID**, then enable
  the node. Keep its HTML parse mode for the escaped message.

To turn either off, right-click that node and choose **Deactivate** (or use its
disabled toggle). Re-enable it the same way. Publish changes to apply them to
scheduled runs. You can turn both off; the backup still runs.

Messages show the VPS, completion time in Moscow, apps, snapshot IDs, newly
uploaded bytes, temporary-file cleanup and cloud capacity. A recent-success skip
is clearly marked and shows the previous successful backup time. No new snapshot
is claimed for a skipped run. New uploaded data is not the total restorable size.
Discord uses a coloured embed with grouped fields and a cloud usage bar;
Telegram uses headings and compact application rows. Skips have a different
status colour from completed backups. The usage bar appears only when both total
and free capacity are known.

**Read cloud space** runs this read-only command on the backup server:

```bash
/usr/local/bin/vps-backup --config /etc/vps-backup/server.json cloud-space
```

The capacity is the provider-reported total, used and free space of the configured
remote. For a union, it is the combined pool, including files outside these backups.
It does not mean every individual account can accept any size of upload. Unsupported
or unavailable capacity is shown as unavailable; missing values stay unknown.
This lookup and each notification use **Continue on Fail**, so a failure there
cannot block the other channel or change the completed backup's result. Check node
outputs for delivery errors. The workflow does not send backup-failure alerts.
Backups started directly by systemd do not pass through this n8n notification flow.
