import { app, BrowserWindow, ipcMain } from "electron";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Import file utilities
import { saveSchedulesToFile, loadSchedulesFromFile, saveWorkflowsToFile, loadWorkflowsFromFile, saveMCPConfigToFile, loadMCPConfigFromFile, saveLLMConfigToFile, loadLLMConfigFromFile, triggerWorkflow, saveSqlFile, loadSqlFile, loadWorkflowRuns, loadLatestWorkflowResult, loadWorkflowResult, clearWorkflowOutputs, getPathsInfo } from './fileUtils.js';

// IPC handlers for schedule operations
ipcMain.handle('schedules:load', async () => {
  try {
    const schedules = loadSchedulesFromFile();
    return schedules;
  } catch (error) {
    console.error('Error loading schedules:', error);
    return [];
  }
});

ipcMain.handle('schedules:save', async (event, schedules) => {
  try {
    const result = saveSchedulesToFile(schedules);
    return result;
  } catch (error) {
    console.error('Error saving schedules:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for multiple workflows
ipcMain.handle('workflows:load', async () => {
  try {
    const workflows = loadWorkflowsFromFile();
    return workflows;
  } catch (error) {
    console.error('Error loading workflows:', error);
    return [];
  }
});

ipcMain.handle('workflows:save', async (event, workflows) => {
  try {
    console.log('Electron main: workflows:save IPC called with data:', workflows);
    const result = saveWorkflowsToFile(workflows);
    console.log('Electron main: saveWorkflowsToFile result:', result);
    return result;
  } catch (error) {
    console.error('Error saving workflows:', error);
    return { success: false, error: error.message };
  }
});

// IPC to trigger a specific workflow
ipcMain.handle('workflows:trigger', async (event, workflowName) => {
  try {
    const result = triggerWorkflow(workflowName);
    return result;
  } catch (error) {
    console.error('Error triggering workflow:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for MCP server configuration
ipcMain.handle('mcp-config:load', async () => {
  try {
    const config = loadMCPConfigFromFile();
    return config;
  } catch (error) {
    console.error('Error loading MCP config:', error);
    return { servers: {}, inputs: [] };
  }
});

ipcMain.handle('mcp-config:save', async (event, config) => {
  try {
    const result = saveMCPConfigToFile(config);
    return result;
  } catch (error) {
    console.error('Error saving MCP config:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for LLM configuration
ipcMain.handle('llm-config:load', async () => {
  try {
    const config = loadLLMConfigFromFile();
    return config;
  } catch (error) {
    console.error('Error loading LLM config:', error);
    return { llms: {} };
  }
});

ipcMain.handle('llm-config:save', async (event, config) => {
  try {
    const result = saveLLMConfigToFile(config);
    return result;
  } catch (error) {
    console.error('Error saving LLM config:', error);
    return { success: false, error: error.message };
  }
});

// IPC to save SQL file
ipcMain.handle('sql:save', async (event, filename, content) => {
  try {
    const result = saveSqlFile(filename, content);
    return result;
  } catch (error) {
    console.error('Error saving SQL file:', error);
    return { success: false, error: error.message };
  }
});

// IPC to load SQL file
ipcMain.handle('sql:load', async (event, relativePath) => {
  try {
    const result = loadSqlFile(relativePath);
    return result;
  } catch (error) {
    console.error('Error loading SQL file:', error);
    return { success: false, error: error.message };
  }
});

// IPC to load workflow execution runs
ipcMain.handle('workflow-runs:load', async () => {
  try {
    const runs = loadWorkflowRuns();
    return runs;
  } catch (error) {
    console.error('Error loading workflow runs:', error);
    return [];
  }
});

// IPC to load latest workflow result details
ipcMain.handle('workflow-result:latest', async () => {
  try {
    const result = loadLatestWorkflowResult();
    return result;
  } catch (error) {
    console.error('Error loading latest workflow result:', error);
    return null;
  }
});

// IPC to load specific workflow result by filename
ipcMain.handle('workflow-result:load', async (event, filename) => {
  try {
    const result = loadWorkflowResult(filename);
    return result;
  } catch (error) {
    console.error('Error loading workflow result:', error);
    return null;
  }
});

// IPC to clear all workflow output files
ipcMain.handle('workflow-outputs:clear', async () => {
  try {
    const result = clearWorkflowOutputs();
    return result;
  } catch (error) {
    console.error('Error clearing workflow outputs:', error);
    return { success: false, error: error.message };
  }
});

// IPC to get paths debug info
ipcMain.handle('debug:paths', async () => {
  try {
    return getPathsInfo();
  } catch (error) {
    console.error('Error getting paths info:', error);
    return { error: error.message };
  }
});

// Docker management functions
// Get path to bundled docker-compose.yml (in resources)
function getBundledDockerComposePath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'agent', 'docker-compose.yml');
  }
  return path.join(__dirname, '..', '..', 'agent', 'docker-compose.yml');
}

// Get writable agent path (for data, triggers, output)
function getWritableAgentPath() {
  if (app.isPackaged) {
    return path.join(app.getPath('userData'), 'agent');
  }
  return path.join(__dirname, '..', '..', 'agent');
}

// Create a docker-compose.yml that uses the writable paths for packaged app
function createDockerComposeForPackaged() {
  if (!app.isPackaged) {
    return getBundledDockerComposePath();
  }
  
  const writableAgentDir = getWritableAgentPath();
  const bundledAgentDir = path.join(process.resourcesPath, 'agent');
  
  // Create a modified docker-compose.yml in the writable directory
  const composeContent = `services:
  agent:
    build: "${bundledAgentDir.replace(/\\/g, '/')}"
    container_name: oncall-agent
    restart: unless-stopped
    environment:
      - NODE_ENV=production
      - LOG_LEVEL=info
      - WORKFLOWS_FILE=/app/data/config/workflows.json
      - TRIGGER_DIR=/app/data/config/triggers
      - OUTPUT_DIR=/app/output
      - SCHEDULER_FILE=/app/data/config/schedules.json
    volumes:
      # Mount writable data directory for config (schedules, workflows, triggers, sql)
      - "${writableAgentDir.replace(/\\/g, '/')}/data:/app/data"
      
      # Mount writable output directory
      - "${writableAgentDir.replace(/\\/g, '/')}/output:/app/output"
      
      # Mount writable logs directory
      - "${writableAgentDir.replace(/\\/g, '/')}/logs:/app/logs"
    networks:
      - oncall-network

networks:
  oncall-network:
    driver: bridge
`;
  
  const writableComposePath = path.join(writableAgentDir, 'docker-compose.yml');
  
  // Ensure directory exists
  if (!fs.existsSync(writableAgentDir)) {
    fs.mkdirSync(writableAgentDir, { recursive: true });
  }
  
  fs.writeFileSync(writableComposePath, composeContent);
  console.log('Created docker-compose.yml at:', writableComposePath);
  
  return writableComposePath;
}

function getDockerComposePath() {
  return createDockerComposeForPackaged();
}

// IPC: Check if Docker is available
ipcMain.handle('docker:check', async () => {
  try {
    await execAsync('docker --version');
    await execAsync('docker-compose --version');
    return { available: true, error: null };
  } catch (error) {
    return { available: false, error: 'Docker Desktop is not installed or not running' };
  }
});

// IPC: Start Docker services
ipcMain.handle('docker:start', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout, stderr } = await execAsync(
      `docker-compose -f "${composePath}" up -d --build`,
      { cwd: agentDir }
    );
    
    return { success: true, message: 'Docker services started successfully', output: stdout };
  } catch (error) {
    console.error('Docker start error:', error);
    return { success: false, error: error.message };
  }
});

// IPC: Stop Docker services
ipcMain.handle('docker:stop', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout, stderr } = await execAsync(
      `docker-compose -f "${composePath}" down`,
      { cwd: agentDir }
    );
    
    return { success: true, message: 'Docker services stopped successfully', output: stdout };
  } catch (error) {
    console.error('Docker stop error:', error);
    return { success: false, error: error.message };
  }
});

// IPC: Get Docker container status
ipcMain.handle('docker:status', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout } = await execAsync(
      `docker-compose -f "${composePath}" ps --format json`,
      { cwd: agentDir }
    );
    
    // Parse JSON output
    const containers = stdout.trim().split('\n')
      .filter(line => line.trim())
      .map(line => {
        try {
          return JSON.parse(line);
        } catch (e) {
          return null;
        }
      })
      .filter(Boolean);
    
    return { success: true, containers };
  } catch (error) {
    console.error('Docker status error:', error);
    return { success: false, containers: [], error: error.message };
  }
});

function createWindow() {
  const win = new BrowserWindow({
    title: "OnCall Agent",
    width: 1200,
    height: 800,
    icon: path.join(__dirname, "..", "public", "favicon.ico"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  // Load from built files in production, localhost in development
  if (app.isPackaged) {
    // In packaged app, load from asar
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    win.loadURL("http://localhost:5173");
  }
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});