const RULE_LABELS = {
  'blocked-domain': 'Blocked site',
  'domain-not-allowed': 'Site not allowed',
  'blocked-app': 'Blocked app',
  'blocked-app-running': 'Blocked app running',
  'app-not-allowed': 'App not allowed',
  'too-many-switches': 'Frequent switching',
  'paste-burst': 'Paste burst',
  'multiple-monitors': 'Multiple monitors',
  'remote-or-vm-tool': 'Remote / VM tool',
  'agent-restart': 'Agent restarted',
  'agent-lost': 'Agent lost',
}

export function ruleLabel(rule) {
  return RULE_LABELS[rule] || rule
}
