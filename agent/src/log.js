const ts = () => new Date().toISOString();
const fmt = (lvl, scope, msg, extra) =>
  `${ts()} ${lvl} [${scope}] ${msg}${extra ? ' ' + (extra instanceof Error ? extra.stack || extra.message : JSON.stringify(extra)) : ''}`;

export const logger = (scope) => ({
  info: (m, e) => console.log(fmt('INFO', scope, m, e)),
  warn: (m, e) => console.warn(fmt('WARN', scope, m, e)),
  error: (m, e) => console.error(fmt('ERROR', scope, m, e)),
});
