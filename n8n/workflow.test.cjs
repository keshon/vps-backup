const { test } = require('node:test');
const assert = require('node:assert/strict');
const workflow = require('./workflow.json');
const node = name => workflow.nodes.find(node => node.name === name);
const runCode = (name, input) => new Function('$input', '$execution', node(name).parameters.jsCode)(
  { first: () => ({ json: input }) }, { id: '42' });

test('template has no credentials and notification switches are independent', () => {
  assert.equal(workflow.active, false);
  for (const item of workflow.nodes) assert.equal(item.credentials, undefined);
  assert.equal(node('Settings').parameters.assignments.assignments[0].value, '');
  assert.equal(node('Telegram').parameters.chatId, '');
  for (const name of ['Discord', 'Telegram']) assert.equal(node(name).disabled, true);
  assert.deepEqual(workflow.connections['Format backup summary'].main[0].map(edge => edge.node), ['Discord', 'Telegram']);
  for (const [source, outputs] of Object.entries(workflow.connections)) {
    assert.ok(node(source));
    for (const branch of outputs.main) for (const edge of branch) assert.ok(node(edge.node));
  }
});

test('SSH command uses Go flags and rejects shell injection in credentials', () => {
  const expression = node('Run backup').parameters.command.slice(3, -2).trim();
  const command = value => new Function('$', 'return ' + expression)(() => ({ first: () => ({ json: { rcloneConfigBase64: value } }) }));
  assert.equal(command('YWJj\n'), "RCLONE_CONFIG_CONTENT='YWJj' /opt/scripts/vps-backup/vps-backup --run --config /root/.config/vps-backup/config.json");
  assert.throws(() => command("abc'; touch /tmp/injection"));
  assert.throws(() => command(''));
});

test('failed commands and failed reports cannot reach success notifications', () => {
  assert.throws(() => runCode('Validate backup result', { code: 1, stdout: '{}' }));
  assert.throws(() => runCode('Validate backup result', { code: 0, stdout: 'not json' }));
  assert.throws(() => runCode('Validate backup result', { code: 0, stdout: '{"status":"failed"}' }));
  assert.throws(() => runCode('Format backup summary', { status: 'failed' }));
});

test('summary reports archives, escapes HTML and excludes unchanged bytes', () => {
  const report = { status: 'success', server_id: '<server>@everyone', finished: '2026-11-18T12:00:00Z', apps: [
    { id: '<app>', archive: 'new.tar.gz', data_added: 1024, unchanged: false },
    { id: 'static', archive: 'old.tar.gz', data_added: 999999, unchanged: true },
  ] };
  const validated = runCode('Validate backup result', { code: 0, stdout: JSON.stringify(report) })[0].json;
  const summary = runCode('Format backup summary', validated)[0].json;
  assert.equal(summary.discordEmbed.description, '1 saved · 1 unchanged');
  assert.equal(summary.discordEmbed.fields.find(field => field.name === 'Uploaded').value, '1.0 KiB');
  assert.match(summary.telegramMessage, /new\.tar\.gz/);
  assert.ok(!summary.telegramMessage.includes('<server>'));
  assert.ok(!summary.telegramMessage.includes('@everyone'));
  assert.equal(summary.discordEmbed.timestamp, report.finished.replace('Z', '.000Z'));
  assert.ok(summary.discordEmbed.fields.find(field => field.name === 'Applications').value.length <= 1024);
});
