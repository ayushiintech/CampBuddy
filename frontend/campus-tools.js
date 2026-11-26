'use strict';

function toolRecords(key, base = state.api) {
  try {
    const records = JSON.parse(preferences.get(key + ':' + base) || '[]');
    return Array.isArray(records) ? records.filter((record) => record && typeof record === 'object') : [];
  } catch {
    return [];
  }
}

function saveToolRecords(key, records, base = state.api) {
  preferences.set(key + ':' + base, JSON.stringify(records));
}

function recordKnowledgeOutcome(base, question, status) {
  if (!['empty_index', 'no_relevant_context', 'success'].includes(status)) return;
  const key = question.trim().replace(/\s+/g, ' ').toLowerCase();
  const records = toolRecords('knowledge_gaps', base);
  const previous = records.find((item) => item.key === key);
  if (!previous && status === 'success') return;
  const remaining = records.filter((item) => item.key !== key);
  if (status !== 'success') {
    remaining.unshift({ key, question, status, count: (previous?.count || 0) + 1 });
  }
  saveToolRecords('knowledge_gaps', remaining.slice(0, 100), base);
  if (base === state.api && byId('gaps').open) renderGaps();
}

function connectTool(id, render) {
  const dialog = byId(id);
  let lastBase = state.api;
  byId('open-' + id).addEventListener('click', () => {
    if (mobile.matches) sidebar(false);
    if (lastBase !== state.api) byId(id + '-results').replaceChildren();
    lastBase = state.api;
    if (render) render();
    dialog.showModal();
  });
}

function expandablePassage(parent, passage) {
  const details = node('details');
  details.append(node('summary', 'Read source passage'), node('p', passage));
  parent.append(details);
}

async function findPassages(query, limit, base) {
  const data = await api('/search', json({ query, top_k: limit }), base);
  if (!Array.isArray(data.results)) throw new Error('The backend did not return search results.');
  return data.results;
}

function bindToolSearch({ form, input, button, results, fallback = '', limit, loading, render }) {
  byId(form).addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = byId(button);
    const query = byId(input).value.trim() || fallback;
    if (!query || submit.disabled) return;
    const base = state.api;
    const output = byId(results);
    submit.disabled = true;
    output.replaceChildren(node('p', loading, 'muted'));
    try {
      const passages = await findPassages(query, limit, base);
      if (base === state.api) render(output, passages, base);
      else output.replaceChildren();
    } catch (error) {
      output.replaceChildren(...(base === state.api ? [node('p', error.message, 'danger')] : []));
    } finally {
      submit.disabled = false;
    }
  });
}

connectTool('notice');
bindToolSearch({
  form: 'notice-form', input: 'notice-text', button: 'notice-submit', results: 'notice-results',
  limit: 5, loading: 'Looking for related passages…',
  render(output, passages) {
    const matches = passages.map((passage) => {
      const card = sourceCard(passage);
      expandablePassage(card, passage.text);
      return card;
    });
    output.replaceChildren(...(matches.length ? matches : [node('p', 'No passages were returned. Check that relevant documents are indexed.', 'muted')]));
  }
});

const months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const monthPattern = months.join('|');
const noticeDates = new RegExp(
  '\\b(?:(?<isoYear>20\\d{2})-(?<isoMonth>\\d{2})-(?<isoDay>\\d{2})|(?<firstMonth>' + monthPattern +
  ')\\s+(?<lastDay>\\d{1,2})(?:st|nd|rd|th)?(?:[,]?\\s+(?<lastYear>\\d{4}))?|(?<firstDay>\\d{1,2})(?:st|nd|rd|th)?\\s+(?<lastMonth>' +
  monthPattern + ')(?:[,]?\\s+(?<otherYear>\\d{4}))?)\\b', 'gi'
);

function campusToday() {
  const date = new Date();
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return localDate.toISOString().slice(0, 10);
}

function datesInPassages(passages, today) {
  const found = new Map();
  for (const passage of passages) {
    if (typeof passage.text !== 'string') continue;
    for (const match of passage.text.matchAll(noticeDates)) {
      const groups = match.groups;
      const year = Number(groups.isoYear || groups.lastYear || groups.otherYear);
      const month = groups.isoMonth ? Number(groups.isoMonth) : months.indexOf((groups.firstMonth || groups.lastMonth).toLowerCase()) + 1;
      const day = Number(groups.isoDay || groups.lastDay || groups.firstDay);
      const date = upcomingDate(year, month, day, today);
      if (!date) continue;
      const prefix = passage.text.slice(Math.max(0, match.index - 40), match.index);
      if (/\b(?:published(?:\s+on)?|issued(?:\s+on)?|posted(?:\s+on)?)\s*:\s*$/i.test(prefix)) continue;
      const afterDate = passage.text.slice(match.index + match[0].length);
      const timeMatch = /^[ \t]*,?[ \t]*(?:at[ \t]+)?(\d{1,2}):(\d{2})(?:[ \t]*([ap]m)\b|(?![ \t]*[ap]m\b))/i.exec(afterDate);
      const time = timeMatch ? noticeTime(timeMatch) : undefined;
      const excerpt = passage.text.slice(Math.max(0, match.index - 95), match.index + match[0].length + 100).trim().replace(/\s+/g, ' ');
      const key = JSON.stringify([date, passage.filename, passage.page_number ?? null, excerpt]);
      if (!found.has(key)) found.set(key, { key, date, time, excerpt, inferredYear: !year, filename: passage.filename, page_number: passage.page_number });
    }
  }
  return [...found.values()].sort((first, second) => first.date.localeCompare(second.date));
}

function noticeTime(match) {
  const [, rawHour, minute, meridiem] = match;
  let hour = Number(rawHour);
  if (Number(minute) > 59 || (meridiem ? hour < 1 || hour > 12 : hour > 23)) return undefined;
  if (meridiem) hour = hour % 12 + (meridiem.toLowerCase() === 'pm' ? 12 : 0);
  return String(hour).padStart(2, '0') + ':' + minute;
}

function upcomingDate(year, month, day, today) {
  const start = year || Number(today.slice(0, 4));
  const end = year ? start : start + 8;
  for (let candidate = start; candidate <= end; candidate++) {
    const calendar = new Date(Date.UTC(candidate, month - 1, day));
    if (calendar.getUTCDate() !== day || calendar.getUTCMonth() !== month - 1) continue;
    const date = calendar.toISOString().slice(0, 10);
    if (date >= today) return date;
  }
  return null;
}

const alarmEditor = byId('alarm-editor');
let editingAlarm;
const alertLists = new Map();

function editAlarm(alert, base) {
  editingAlarm = { alert, base };
  byId('alarm-name').value = (alert.title || alert.excerpt || '').slice(0, 120);
  byId('alarm-date').min = campusToday();
  byId('alarm-date').value = alert.date;
  byId('alarm-time').value = alert.time || '09:00';
  byId('alarm-lead').value = String(alert.lead ?? (alert.date === campusToday() ? 0 : 1440));
  byId('alarm-date-note').textContent = alert.inferredYear ? 'The notice omits the year. Confirm the suggested date and choose a time.' : 'Confirm the date and choose a time for the reminder.';
  byId('alarm-error').textContent = '';
  alarmEditor.showModal();
}

function calendarText(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/\r?\n|\r/g, '\\n').replace(/([;,])/g, '\\$1');
}

function calendarStamp(time) {
  return new Date(time).toISOString().replace(/[-:]|\.\d{3}/g, '');
}

function calendarFile(alarm, now = Date.now()) {
  const minutes = alarm.lead ?? 1440;
  const at = alarm.at || new Date(alarm.date + 'T09:00').getTime();
  return [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//CampBuddy//Campus Alarm//EN',
    'BEGIN:VEVENT', 'UID:' + encodeURIComponent(alarm.key) + '@campbuddy.local',
    'DTSTAMP:' + calendarStamp(now), 'DTSTART:' + calendarStamp(at),
    'SUMMARY:' + calendarText(alarm.title || 'Campus reminder'),
    'DESCRIPTION:' + calendarText([alarm.excerpt, alarm.filename, alarm.page_number && 'Page ' + alarm.page_number].filter(Boolean).join(' · ')),
    'BEGIN:VALARM', 'ACTION:DISPLAY', 'TRIGGER:' + (minutes ? '-PT' + minutes + 'M' : 'PT0M'),
    'DESCRIPTION:' + calendarText(alarm.title || 'Campus reminder'),
    'END:VALARM', 'END:VEVENT', 'END:VCALENDAR'
  ].map(foldCalendarLine).join('\r\n') + '\r\n';
}

function foldCalendarLine(line) {
  const encoder = new TextEncoder();
  let result = '';
  let bytes = 0;
  for (const character of line) {
    const size = encoder.encode(character).length;
    if (bytes + size > 75) {
      result += '\r\n ';
      bytes = 1;
    }
    result += character;
    bytes += size;
  }
  return result;
}

function downloadAlarm(alarm) {
  const file = new Blob([calendarFile(alarm)], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(file);
  const link = node('a');
  link.href = url;
  link.download = 'campbuddy-' + alarm.date + '.ics';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function alarmButton(text, action) {
  const button = node('button', text, 'text-button');
  button.type = 'button';
  button.addEventListener('click', action);
  return button;
}

function alertCard(alert, base, saved, inSavedList = false) {
  const card = sourceCard(alert);
  const timing = alert.date + (alert.time ? ' at ' + alert.time : '') +
    (alert.date === campusToday() ? ' · Today' : '') + (alert.inferredYear ? ' · Suggested year' : '');
  card.prepend(node('strong', (alert.title ? alert.title + ' · ' : '') + timing + ' · '));
  if (alert.excerpt) expandablePassage(card, alert.excerpt);
  if (inSavedList) {
    card.append(alarmButton(alert.at ? 'Edit reminder' : 'Set reminder', () => editAlarm(alert, base)));
    if (alert.at) {
      card.append(alarmButton('Download calendar reminder', () => downloadAlarm(alert)));
    }
    card.append(alarmButton('Remove reminder', () => {
      saveToolRecords('saved_alerts', toolRecords('saved_alerts', base).filter((item) => item.key !== alert.key), base);
      refreshAlarmLists(base);
    }));
  } else {
    const action = alarmButton(saved ? 'Reminder saved' : 'Create reminder', () => editAlarm(alert, base));
    action.disabled = saved;
    card.append(action);
  }
  return card;
}

function alarmDay(date, offset = 0) {
  const day = new Date(date + 'T12:00:00Z');
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

function alarmDateLabel(date, options) {
  return new Intl.DateTimeFormat(undefined, { ...options, timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'));
}

function alarmName(alarm) {
  return alarm.title || alarm.excerpt || 'Campus deadline';
}

function renderSavedAlerts() {
  const records = toolRecords('saved_alerts').sort((first, second) =>
    (first.date + (first.time || '')).localeCompare(second.date + (second.time || '')));
  const today = campusToday();
  const upcoming = records.filter((alarm) => alarm.date >= today);
  const weekEnd = alarmDay(today, 6);
  const weekCount = upcoming.filter((alarm) => alarm.date <= weekEnd).length;
  byId('alarm-week-summary').textContent = weekCount
    ? weekCount + (weekCount === 1 ? ' deadline in the next 7 days' : ' deadlines in the next 7 days')
    : 'No deadlines in the next 7 days';
  const cards = upcoming.slice(0, 3).map((alarm) => {
    const card = node('button', null, 'alarm-deadline');
    card.type = 'button';
    card.append(node('span', alarmDateLabel(alarm.date, { month: 'short', day: 'numeric' }), 'alarm-date-label'));
    card.append(node('strong', alarmName(alarm)), node('small', alarm.time ? alarm.time + (alarm.filename ? ' · ' + alarm.filename : '') : alarm.filename || 'Time to be confirmed'));
    card.addEventListener('click', () => editAlarm(alarm, state.api));
    return card;
  });
  byId('alarm-upcoming').replaceChildren(...(cards.length ? cards : [node('p', 'Add a reminder to see upcoming deadlines here.', 'alarm-empty muted')]));
  byId('saved-alerts').replaceChildren(...(records.length ? records.map((alert) => alertCard(alert, state.api, true, true)) : [node('p', 'No reminders saved on this device.', 'alarm-empty muted')]));

  const week = Array.from({ length: 7 }, (_, index) => {
    const date = alarmDay(today, index);
    const day = node('div', null, 'alarm-day');
    day.append(node('h3', index ? alarmDateLabel(date, { weekday: 'short', month: 'short', day: 'numeric' }) : 'Today · ' + alarmDateLabel(date, { month: 'short', day: 'numeric' })));
    const events = upcoming.filter((alarm) => alarm.date === date);
    for (const alarm of events) {
      const item = node('button', null, 'alarm-event');
      item.type = 'button';
      item.append(node('strong', alarmName(alarm)), node('small', alarm.time || 'Time to be confirmed'));
      item.addEventListener('click', () => editAlarm(alarm, state.api));
      day.append(item);
    }
    if (!events.length) day.append(node('p', 'No deadlines', 'muted'));
    return day;
  });
  byId('alarm-week').replaceChildren(...week);
}

function renderAlertCandidates(output, passages, base) {
  const candidates = datesInPassages(passages, campusToday());
  alertLists.set(output, { candidates, base });
  refreshAlarmLists(base);
}

function refreshAlarmLists(base) {
  renderSavedAlerts();
  const saved = new Set(toolRecords('saved_alerts', base).map((item) => item.key));
  for (const [output, list] of alertLists) {
    if (list.base !== base) continue;
    const cards = list.candidates.slice(0, 30).map((alert) => alertCard(alert, base, saved.has(alert.key)));
    output.replaceChildren(...(cards.length ? cards : [node('p', 'No upcoming dates found. Try a date like 15 December or 2027-12-15.', 'muted')]));
  }
}

byId('alarm-editor-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const { alert, base } = editingAlarm;
  const title = byId('alarm-name').value.trim();
  const date = byId('alarm-date').value;
  const time = byId('alarm-time').value;
  const lead = Number(byId('alarm-lead').value);
  const at = new Date(date + 'T' + time).getTime();
  if (!title || !Number.isFinite(at) || at - lead * 60000 <= Date.now()) {
    byId('alarm-error').textContent = 'Enter a title and choose a reminder time in the future.';
    return;
  }
  const reminder = { ...alert, title, date, time, lead, at, inferredYear: false };
  const records = toolRecords('saved_alerts', base).filter((item) => item.key !== alert.key);
  saveToolRecords('saved_alerts', [...records, reminder], base);
  alarmEditor.close();
  refreshAlarmLists(base);
});

let alarmBase = state.api;
function setAlarmView(open) {
  byId('main').hidden = open;
  byId('alerts').hidden = !open;
  byId('open-alerts').setAttribute('aria-current', open ? 'page' : 'false');
}

function refreshAlarmDashboard() {
  if (alarmBase !== state.api) {
    byId('alarm-notice-results').replaceChildren();
    byId('alerts-results').replaceChildren();
    alertLists.clear();
    alarmBase = state.api;
  }
  renderSavedAlerts();
}

byId('open-alerts').addEventListener('click', () => {
  if (mobile.matches) sidebar(false);
  refreshAlarmDashboard();
  setAlarmView(true);
  byId('alerts-title').focus();
});
byId('return-chat').addEventListener('click', () => {
  setAlarmView(false);
  question.focus();
});
byId('alarm-menu').addEventListener('click', () => byId('menu').click());
byId('new-reminder').addEventListener('click', () => editAlarm({
  key: 'manual-' + Date.now() + '-' + Math.random().toString(36).slice(2),
  date: campusToday(), filename: 'Personal reminder'
}, state.api));
byId('alarm-notice-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const notice = byId('alarm-notice').value.trim();
  if (notice) renderAlertCandidates(byId('alarm-notice-results'), [{ filename: 'Pasted notice', text: notice }], state.api);
});
bindToolSearch({
  form: 'alerts-form', input: 'alert-topic', button: 'alerts-submit', results: 'alerts-results',
  fallback: 'upcoming campus deadlines assignments registration examinations exams scholarships tuition events dates', limit: 20,
  loading: 'Looking for upcoming dates…', render: renderAlertCandidates
});

function renderGaps() {
  const records = toolRecords('knowledge_gaps');
  byId('gap-summary').textContent = records.length + (records.length === 1 ? ' unresolved question' : ' unresolved questions');
  const cards = records.map((record) => {
    const card = node('div', null, 'result');
    const reason = record.status === 'empty_index' ? 'No indexed documents' : 'No relevant source passages';
    const retry = node('button', 'Retry in chat', 'text-button');
    retry.type = 'button';
    retry.addEventListener('click', () => {
      byId('gaps').close();
      question.value = record.question;
      updateChat();
      question.focus();
    });
    card.append(node('strong', record.question), node('span', reason + ' · ' + record.count + (record.count === 1 ? ' attempt' : ' attempts')), retry);
    return card;
  });
  byId('gap-results').replaceChildren(...(cards.length ? cards : [node('p', 'No unanswered questions recorded for this backend.', 'muted')]));
}

connectTool('gaps', renderGaps);
