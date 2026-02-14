/**
 * Cron Utilities
 * Shared functions for converting between local time and UTC cron expressions
 */

/**
 * Convert local time string to UTC hours and minutes
 * @param {string} timeString - Time in HH:MM format (e.g., "10:30")
 * @returns {Object} Object with utcHour and utcMinute as strings
 */
export const localTimeToUTC = (timeString) => {
  const [hour, minute] = timeString.split(':');
  const localDate = new Date();
  localDate.setHours(parseInt(hour || '0'), parseInt(minute || '0'), 0, 0);
  
  return {
    utcHour: localDate.getUTCHours().toString(),
    utcMinute: localDate.getUTCMinutes().toString()
  };
};

/**
 * Convert Date object to UTC hours and minutes
 * @param {Date} dateObj - Date object with time set
 * @returns {Object} Object with utcHour and utcMinute as strings
 */
export const dateToUTC = (dateObj) => {
  return {
    utcHour: dateObj.getUTCHours().toString(),
    utcMinute: dateObj.getUTCMinutes().toString()
  };
};

/**
 * Generate cron expression in UTC based on recurrence pattern
 * @param {string} utcMinute - UTC minute (0-59)
 * @param {string} utcHour - UTC hour (0-23)
 * @param {string} recurrence - Recurrence pattern: 'daily', 'weekly', 'monthly'
 * @returns {string} Cron expression in UTC
 */
export const generateCronExpression = (utcMinute, utcHour, recurrence) => {
  switch (recurrence) {
    case 'weekly':
      return `${utcMinute} ${utcHour} * * 1`; // Monday
    case 'monthly':
      return `${utcMinute} ${utcHour} 1 * *`; // 1st of month
    case 'daily':
    default:
      return `${utcMinute} ${utcHour} * * *`; // Daily
  }
};

/**
 * Convert local time string to UTC cron expression
 * @param {string} timeString - Time in HH:MM format (e.g., "10:30")
 * @param {string} recurrence - Recurrence pattern: 'daily', 'weekly', 'monthly'
 * @returns {string} Cron expression in UTC
 */
export const localTimeToCron = (timeString, recurrence = 'daily') => {
  const { utcHour, utcMinute } = localTimeToUTC(timeString);
  return generateCronExpression(utcMinute, utcHour, recurrence);
};

/**
 * Convert Date object to UTC cron expression
 * @param {Date} dateObj - Date object with time set
 * @param {string} recurrence - Recurrence pattern: 'daily', 'weekly', 'monthly'
 * @returns {string} Cron expression in UTC
 */
export const dateToCron = (dateObj, recurrence = 'daily') => {
  const { utcHour, utcMinute } = dateToUTC(dateObj);
  return generateCronExpression(utcMinute, utcHour, recurrence);
};

/**
 * Format Date object to local time string (HH:MM)
 * @param {Date} dateObj - Date object
 * @returns {string} Time string in HH:MM format
 */
export const dateToLocalTimeString = (dateObj) => {
  return dateObj.toTimeString().split(' ')[0].substring(0, 5);
};

/**
 * Parse cron expression to local time string
 * @param {string} cronExpression - Cron expression (e.g., "30 18 * * *")
 * @returns {string} Time string in HH:MM format (local time)
 */
export const cronToLocalTime = (cronExpression) => {
  if (!cronExpression) return '09:00';
  
  const parts = cronExpression.split(' ');
  if (parts.length < 2) return '09:00';
  
  // Parse UTC time from cron
  const utcMinute = parseInt(parts[0]);
  const utcHour = parseInt(parts[1]);
  
  // Validate parsed values
  if (isNaN(utcMinute) || isNaN(utcHour)) return '09:00';
  
  // Create a date with a fixed date (to avoid date boundary issues)
  // Use a date in the middle of a month to avoid edge cases
  const utcDate = new Date(Date.UTC(2024, 0, 15, utcHour, utcMinute, 0, 0));
  
  const localHour = utcDate.getHours().toString().padStart(2, '0');
  const localMinute = utcDate.getMinutes().toString().padStart(2, '0');
  
  return `${localHour}:${localMinute}`;
};
