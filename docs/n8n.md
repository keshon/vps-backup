# Schedule with n8n

Both storage modes use the same workflow. The VPS's configuration chooses the
format; n8n only connects through SSH and runs the client.

1. Install and test the VPS client using [setup](setup.md).
2. Import [deploy/n8n-backup.json](../deploy/n8n-backup.json) into n8n.
3. In **Run backup**, select your SSH credential. The template contains no
   credentials. Use root, or an account permitted to run the command with `sudo -n`.
4. Review the Schedule Trigger and workflow timezone, then publish the workflow.

The example runs Wednesdays at 05:00 Moscow time. Its SSH command is:

```bash
/usr/local/bin/vps-backup --config /etc/vps-backup/client.json run --min-age-hours 20
```

The validation node requires SSH exit code 0 and JSON `status: "success"`.
Skipping a recent successful backup counts as success. Failures fail the workflow;
the template does not send alerts. The manual trigger uses the same recent-success
guard. To force another backup, run `sudo vps-backup run --force` on the VPS.

For the commands to disable the local timer or add a systemd fallback, see
[the scheduling examples](setup.md#n8n-only). Changing the workflow does not change
the VPS's timer. Set the workflow timeout long enough for your backup to finish.
