import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import logger from '../shared/logger.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Cron parser compatibility wrapper
async function parseCron(expression, options = {}) {
  const mod = await import('cron-parser');
  
  // Merge default options with provided options
  const parseOptions = { tz: 'UTC', ...options };

  if (mod.CronExpressionParser?.parse)
    return mod.CronExpressionParser.parse(expression, parseOptions);

  if (mod.default?.CronExpressionParser?.parse)
    return mod.default.CronExpressionParser.parse(expression, parseOptions);

  if (typeof mod.default?.parseExpression === 'function')
    return mod.default.parseExpression(expression, parseOptions);

  if (typeof mod.parseExpression === 'function')
    return mod.parseExpression(expression, parseOptions);

  throw new Error('Unsupported cron-parser API');
}

// Configuration
const SCHEDULER_FILE = process.env.SCHEDULER_FILE || path.join(__dirname, '..', '..', 'data', 'config', 'schedules.json');
const TRIGGER_DIR = process.env.TRIGGER_DIR || path.join(__dirname, '..', '..', 'data', 'config', 'triggers');
const MANUAL_TRIGGER_FILE = process.env.MANUAL_TRIGGER_FILE || path.join(__dirname, '..', '..', 'data', 'manual-trigger.flag');

// Ensure trigger folder exists
if (!fs.existsSync(TRIGGER_DIR)) {
  fs.mkdirSync(TRIGGER_DIR, { recursive: true });
}

// Load all schedules
function loadSchedules() {
  if (!fs.existsSync(SCHEDULER_FILE)) return [];

  try {
    const raw = JSON.parse(fs.readFileSync(SCHEDULER_FILE, 'utf8'));
    return Array.isArray(raw) ? raw : [raw];
  } catch (e) {
    logger.error('Invalid schedules.json:', e.message);
    return [];
  }
}

// Should run this minute?
async function shouldRun(schedule) {
  const now = new Date();
  const windowStart = new Date(now.getTime() - 60 * 1000);

  try {
    // Parse cron with currentDate set to windowStart to get next occurrence from that point
    const expr = await parseCron(schedule, { currentDate: windowStart });

    const nextDate = expr.next();

    const candidate =
      nextDate instanceof Date
        ? nextDate
        : nextDate.toDate
        ? nextDate.toDate()
        : new Date(nextDate.toString());

    const shouldTrigger = candidate > windowStart && candidate <= now;
    
    if (shouldTrigger) {
      logger.info(`Schedule match: ${schedule} - Next: ${candidate.toISOString()}, Window: ${windowStart.toISOString()} - ${now.toISOString()}`);
    }
    
    return shouldTrigger;

  } catch (e) {
    logger.warn('Invalid cron expression:', schedule, e.message);
    return false;
  }
}

// Create trigger file
function triggerSchedule(schedule) {
  const workflowName = schedule.workflow || schedule.name;
  const safeName = workflowName.replace(/\s+/g, '_');
  const filePath = path.join(TRIGGER_DIR, `${safeName}.flag`);
  fs.writeFileSync(filePath, Date.now().toString());
  logger.info(`Triggered schedule: ${schedule.name} -> workflow: ${workflowName}`);
}

// Handle manual trigger
function checkManualTrigger(schedules) {
  if (!fs.existsSync(MANUAL_TRIGGER_FILE)) return;

  logger.info('🔔 Manual Trigger detected — triggering ALL schedules');

  schedules.forEach(schedule => {
    if (schedule.enabled) triggerSchedule(schedule);
  });

  // Consume flag
  fs.unlinkSync(MANUAL_TRIGGER_FILE);
}

// Tick loop
async function tick() {
  logger.info('Scheduler tick: ' + new Date().toISOString());

  const schedules = loadSchedules();
  if (schedules.length === 0) {
    logger.info('No schedules found');
    return;
  }

  // Manual trigger handler (high priority)
  checkManualTrigger(schedules);

  // Cron scanning
  for (const schedule of schedules) {
    if (!schedule.enabled) {
      logger.debug(`Schedule disabled: ${schedule.name}`);
      continue;
    }

    if (!schedule.schedule) {
      logger.warn(`Schedule has no cron schedule: ${schedule.name}`);
      continue;
    }

    if (await shouldRun(schedule.schedule)) {
      triggerSchedule(schedule);
    }
  }
}

// Start scheduler
logger.info('Starting Scheduler Service');
logger.info(`Schedules file: ${SCHEDULER_FILE}`);
logger.info(`Trigger directory: ${TRIGGER_DIR}`);

tick();
setInterval(tick, 60 * 1000);
