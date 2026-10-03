// The dashboards this console can actually render. A role's own list comes from the server
// (GET /auth/me); this is only the intersection check, so a dashboard the server grants but
// the console has not built - today, Tenant Health - is not offered as a dead tab or card.
export const RENDERED_DASHBOARDS: ReadonlySet<string> = new Set([
  "analyst", "ews", "rfa", "board", "supervisor", "realtime",
  "aml", "account360", "investigator", "model", "risk_manager", "inspection",
])
