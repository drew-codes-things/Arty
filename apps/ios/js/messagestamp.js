const LOCALE = "en-GB";
const MS_PER_DAY = 86400000;
const LAST_WEEKDAY_NAMED = 6;

const weekdayFormat = new Intl.DateTimeFormat(LOCALE, { weekday: "short" });
const dateFormat = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short" });
const dateWithYearFormat = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", year: "numeric" });
const clockFormat = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function daysAgo(moment, now) {
  return Math.round((startOfDay(now) - startOfDay(moment)) / MS_PER_DAY);
}

function dayLabel(moment, now) {
  const days = daysAgo(moment, now);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days > 1 && days <= LAST_WEEKDAY_NAMED) return weekdayFormat.format(moment);
  if (moment.getFullYear() === now.getFullYear()) return dateFormat.format(moment);
  return dateWithYearFormat.format(moment);
}

export function stampLabel(milliseconds, now = new Date()) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "";
  const moment = new Date(milliseconds);
  return `${dayLabel(moment, now)} ${clockFormat.format(moment)}`;
}
