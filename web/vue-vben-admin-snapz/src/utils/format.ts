export function formatDate(value?: string) {
  if (!value) {
    return '-';
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return value;
  }
  return new Date(parsed).toLocaleString();
}

export function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let scaled = value;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const precision = scaled >= 10 || unit === 0 ? 0 : 1;
  return `${scaled.toFixed(precision)} ${units[unit]}`;
}

export function formatSpeed(value?: number) {
  return `${formatBytes(Number(value || 0))}/s`;
}

export function formatEta(value?: number | null) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '-';
  }
  if (seconds < 1) {
    return '<1s';
  }
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  const secs = whole % 60;
  if (minutes <= 0) {
    return `${secs}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (hours <= 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${hours}h ${remMinutes}m`;
}
