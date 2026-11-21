'use strict';

const STAMP_LOCALE = 'en-GB';
const STAMP_MS_PER_DAY = 86400000;
const STAMP_LAST_WEEKDAY_NAMED = 6;

const stampWeekday = new Intl.DateTimeFormat(STAMP_LOCALE, { weekday: 'short' });
const stampDate = new Intl.DateTimeFormat(STAMP_LOCALE, { day: 'numeric', month: 'short' });
const stampDateWithYear = new Intl.DateTimeFormat(STAMP_LOCALE, { day: 'numeric', month: 'short', year: 'numeric' });
const stampClock = new Intl.DateTimeFormat(STAMP_LOCALE, { hour: '2-digit', minute: '2-digit' });

function stampStartOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function stampDaysAgo(moment, now) {
  return Math.round((stampStartOfDay(now) - stampStartOfDay(moment)) / STAMP_MS_PER_DAY);
}

function stampDayLabel(moment, now) {
  const days = stampDaysAgo(moment, now);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days > 1 && days <= STAMP_LAST_WEEKDAY_NAMED) return stampWeekday.format(moment);
  if (moment.getFullYear() === now.getFullYear()) return stampDate.format(moment);
  return stampDateWithYear.format(moment);
}

function stampLabel(milliseconds, now = new Date()) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '';
  const moment = new Date(milliseconds);
  return `${stampDayLabel(moment, now)} ${stampClock.format(moment)}`;
}
