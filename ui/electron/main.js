import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { exec, spawn } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Track currently running workflows
const runningWorkflows = new Set();

// Determine base path for agent files - same logic as fileUtils.js
// In packaged app, use a writable folder in user's app data
// In dev, agent files are in ../agent from ui folder
const getAgentPath = () => {
  if (app.isPackaged) {
    return path.join(app.getPath('userData'), 'agent');
  } else {
    return path.join(__dirname, '..', '..', 'agent');
  }
};

// Lazy-initialized paths (deferred until app is ready)
let triggersDir = null;
let outputDir = null;

const getTriggersDir = () => {
  if (!triggersDir) {
    triggersDir = path.join(getAgentPath(), 'data', 'config', 'triggers');
  }
  return triggersDir;
};

const getOutputDir = () => {
  if (!outputDir) {
    outputDir = path.join(getAgentPath(), 'output');
  }
  return outputDir;
};

// Monitor workflow completion by polling for new output files
function monitorWorkflowCompletion(workflowName) {
  const outputDirPath = getOutputDir();
  let lastOutputTime = Date.now();

  if (fs.existsSync(outputDirPath)) {
    const files = fs.readdirSync(outputDirPath);
    const latestFile = files
      .filter(f => f.endsWith('.json'))
      .map(f => fs.statSync(path.join(outputDirPath, f)).mtimeMs)
      .sort((a, b) => b - a)[0];
    if (latestFile) lastOutputTime = latestFile;
  }

  const checkCompletion = setInterval(() => {
    try {
      if (fs.existsSync(outputDirPath)) {
        const files = fs.readdirSync(outputDirPath);
        const newestFile = files
          .filter(f => f.endsWith('.json') && !f.includes('_latest'))
          .map(f => ({ name: f, time: fs.statSync(path.join(outputDirPath, f)).mtimeMs }))
          .sort((a, b) => b.time - a.time)[0];

        if (newestFile && newestFile.time > lastOutputTime) {
          console.log(`Workflow completed (new output): ${workflowName}, file: ${newestFile.name}`);
          runningWorkflows.delete(workflowName);
          clearInterval(checkCompletion);
        }
      }
    } catch (err) {
      console.error('Error checking workflow completion:', err);
    }
  }, 2000);

  // Safety timeout
  setTimeout(() => {
    console.log(`Workflow timeout: ${workflowName}`);
    runningWorkflows.delete(workflowName);
    clearInterval(checkCompletion);
  }, 5 * 60 * 1000);
}

// Setup trigger folder watcher for automatic scheduled workflows
function setupTriggerWatcher() {
  const triggersDirPath = getTriggersDir();
  
  console.log('Setting up trigger watcher:', { triggersDir: triggersDirPath, outputDir: getOutputDir() });
  
  if (!fs.existsSync(triggersDirPath)) {
    fs.mkdirSync(triggersDirPath, { recursive: true });
  }
  
  fs.watch(triggersDirPath, (eventType, filename) => {
    if (eventType !== 'rename' || !filename?.endsWith('.flag')) {
      return;
    }

    const flagPath = path.join(triggersDirPath, filename);
    const workflowName = filename.replace('.flag', '').replaceAll('_', ' ');

    // Check if file was created (not deleted) and not already running
    if (fs.existsSync(flagPath) && !runningWorkflows.has(workflowName)) {
      console.log(`Workflow started (trigger watcher): ${workflowName}`);
      runningWorkflows.add(workflowName);
      monitorWorkflowCompletion(workflowName);
    }
  });
}

// Import file utilities
import { saveSchedulesToFile, loadSchedulesFromFile, saveWorkflowsToFile, loadWorkflowsFromFile, saveMCPConfigToFile, loadMCPConfigFromFile, saveLLMConfigToFile, loadLLMConfigFromFile, setLLMApiKey, getLLMApiKeyMasked, getLLMApiKey, hasLLMApiKey, deleteLLMApiKey, triggerWorkflow, runAgentWorkflow, saveSqlFile, loadSqlFile, loadWorkflowRuns, loadLatestWorkflowResult, loadWorkflowResult, clearWorkflowOutputs, getPathsInfo } from './fileUtils.js';

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

// IPC handler to check workflows currently running (tracked in memory)
ipcMain.handle('schedules:inProgress', async () => {
  try {
    const schedules = Array.from(runningWorkflows);
    return { count: schedules.length, schedules };
  } catch (error) {
    console.error('Error checking workflows in progress:', error);
    return { count: 0, schedules: [] };
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
    // Track this workflow as running
    console.log(`Workflow triggered: ${workflowName}`);
    runningWorkflows.add(workflowName);
    
    const result = triggerWorkflow(workflowName);
    
    // Watch for new output files (workflow completed when new output appears)
    if (result.success) {
      monitorWorkflowCompletion(workflowName);
    }
    
    return result;
  } catch (error) {
    runningWorkflows.delete(workflowName);
    console.error('Error triggering workflow:', error);
    return { success: false, error: error.message };
  }
});

// IPC to run an agent with a user query
ipcMain.handle('agent:run', async (event, workflowName, userQuery) => {
  try {
    // Track this workflow as running
    runningWorkflows.add(workflowName);
    
    // Get the sender window to send progress events
    const senderWindow = BrowserWindow.fromWebContents(event.sender);
    
    // Progress callback that sends events to the renderer
    const progressCallback = (progress) => {
      if (senderWindow && !senderWindow.isDestroyed()) {
        senderWindow.webContents.send('agent:progress', progress);
      }
    };
    
    const result = await runAgentWorkflow(workflowName, userQuery, progressCallback);
    
    // Remove from running workflows
    runningWorkflows.delete(workflowName);
    return result;
  } catch (error) {
    // Remove from running workflows on error too
    runningWorkflows.delete(workflowName);
    console.error('Error running agent:', error);
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

// IPC handler for testing MCP server connection
ipcMain.handle('mcp-server:test', async (event, serverName, serverConfig) => {
  try {
    console.log(`Testing connection to MCP server: ${serverName}`);
    
    const { command, args = [], env = {} } = serverConfig;
    
    if (!command) {
      return { success: false, error: 'No command specified' };
    }
    
    // Create a promise that spawns the process and checks if it starts successfully
    return new Promise((resolve) => {
      const processEnv = { ...process.env, ...env };
      const child = spawn(command, args, { 
        env: processEnv,
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: true
      });
      
      let stdout = '';
      let stderr = '';
      let resolved = false;
      
      // Set a timeout for the connection test (5 seconds to allow for slower connections)
      const timeout = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          child.kill();
          // If process was running for 5 seconds without error, consider it successful
          resolve({ 
            success: true, 
            message: `Connected`,
            output: stdout || 'Connection established'
          });
        }
      }, 5000);
      
      child.stdout.on('data', (data) => {
        stdout += data.toString();
        console.log(`[${serverName}] stdout:`, data.toString().substring(0, 200));
      });
      
      child.stderr.on('data', (data) => {
        stderr += data.toString();
        console.log(`[${serverName}] stderr:`, data.toString().substring(0, 200));
        
        // Check for common error patterns
        const errStr = stderr.toLowerCase();
        if (errStr.includes('enoent') || errStr.includes('not found') || errStr.includes('cannot find')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: `Command not found: ${command}` });
          }
        } else if (errStr.includes('econnrefused') || errStr.includes('connection refused')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Connection refused - check host/port' });
          }
        } else if (errStr.includes('authentication') || errStr.includes('password') || errStr.includes('denied')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Authentication failed' });
          }
        } else if (errStr.includes('certificate') || errStr.includes('ssl') || errStr.includes('tls')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'SSL/Certificate error - check NODE_EXTRA_CA_CERTS' });
          }
        } else if (errStr.includes('timeout') || errStr.includes('timed out')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Connection timeout' });
          }
        }
      });
      
      child.on('error', (err) => {
        console.log(`[${serverName}] error:`, err.message);
        if (!resolved) {
          resolved = true;
          clearTimeout(timeout);
          resolve({ success: false, error: err.message });
        }
      });
      
      child.on('exit', (code) => {
        console.log(`[${serverName}] exit code:`, code, 'stderr:', stderr.substring(0, 300));
        if (!resolved) {
          resolved = true;
          clearTimeout(timeout);
          if (code === 0 || code === null) {
            resolve({ 
              success: true, 
              message: `Connected`,
              output: stdout
            });
          } else {
            // Try to extract a meaningful error from stderr
            let errorMsg = `Exit code ${code}`;
            if (stderr) {
              // Get the last meaningful line from stderr
              const lines = stderr.trim().split('\n').filter(l => l.trim());
              if (lines.length > 0) {
                errorMsg = lines.at(-1).substring(0, 50);
              }
            }
            resolve({ 
              success: false, 
              error: errorMsg
            });
          }
        }
      });
    });
  } catch (error) {
    console.error('Error testing MCP server:', error);
    return { success: false, error: error.message };
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

// IPC handlers for LLM API key management (stored in LLM config)
ipcMain.handle('llm-config:set-api-key', async (event, llmName, apiKey) => {
  try {
    const result = setLLMApiKey(llmName, apiKey);
    return result;
  } catch (error) {
    console.error('Error setting API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:get-api-key-masked', async (event, llmName) => {
  try {
    const masked = getLLMApiKeyMasked(llmName);
    return { success: true, masked };
  } catch (error) {
    console.error('Error getting masked API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:has-api-key', async (event, llmName) => {
  try {
    const exists = hasLLMApiKey(llmName);
    return { success: true, exists };
  } catch (error) {
    console.error('Error checking API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:delete-api-key', async (event, llmName) => {
  try {
    const result = deleteLLMApiKey(llmName);
    return result;
  } catch (error) {
    console.error('Error deleting API key:', error);
    return { success: false, error: error.message };
  }
});

// Build provider-specific request config for LLM connection test
function buildLlmTestRequest(provider, model, endpoint, baseUrl, apiKey) {
  const headers = { 'Content-Type': 'application/json' };

  switch (provider) {
    case 'OpenAI':
      return { url: 'https://api.openai.com/v1/models', headers: { ...headers, Authorization: `Bearer ${apiKey}` } };

    case 'Anthropic':
      return {
        url: 'https://api.anthropic.com/v1/messages',
        headers: { ...headers, 'x-api-key': apiKey, 'anthropic-version': '2023-06-01' },
        body: JSON.stringify({ model: model || 'claude-3-5-sonnet-20241022', max_tokens: 1, messages: [] })
      };

    case 'Groq':
      return { url: 'https://api.groq.com/openai/v1/models', headers: { ...headers, Authorization: `Bearer ${apiKey}` } };

    case 'Google':
      return { url: `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`, headers };

    case 'Azure OpenAI':
      if (!endpoint) return { error: 'Azure endpoint URL is required' };
      return { url: `${endpoint}/openai/models?api-version=2024-02-01`, headers: { ...headers, 'api-key': apiKey } };

    case 'Ollama': {
      const ollamaUrl = baseUrl || 'http://localhost:11434';
      return { url: `${ollamaUrl}/api/tags`, headers };
    }

    default:
      return { error: `Unknown provider: ${provider}` };
  }
}

// Parse LLM test error response into a result object
function parseLlmErrorResponse(response, errorText, provider) {
  let errorMsg = `HTTP ${response.status}`;
  try {
    const errorJson = JSON.parse(errorText);
    errorMsg = errorJson.error?.message || errorJson.message || errorMsg;
  } catch {
    errorMsg = errorText.substring(0, 100) || errorMsg;
  }

  // For Anthropic, a 400 error with valid API key means connection works
  // (we send empty messages intentionally to avoid burning tokens)
  if (provider === 'Anthropic' && response.status === 400) {
    return { success: true, message: `Connected to ${provider}` };
  }

  return { success: false, error: errorMsg };
}

// IPC handler for testing LLM connection
ipcMain.handle('llm:test', async (event, llmName, llmConfig) => {
  try {
    console.log(`Testing LLM connection: ${llmName}`);
    
    const { provider, model, endpoint, baseUrl } = llmConfig;
    const apiKey = getLLMApiKey(llmName);
    
    // Ollama doesn't require an API key
    if (provider !== 'Ollama' && !apiKey) {
      return { success: false, error: 'No API key configured' };
    }
    
    const reqConfig = buildLlmTestRequest(provider, model, endpoint, baseUrl, apiKey);
    if (reqConfig.error) {
      return { success: false, error: reqConfig.error };
    }
    
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    
    const fetchOptions = {
      method: reqConfig.body ? 'POST' : 'GET',
      headers: reqConfig.headers,
      signal: controller.signal
    };
    
    if (reqConfig.body) {
      fetchOptions.body = reqConfig.body;
    }
    
    try {
      const response = await fetch(reqConfig.url, fetchOptions);
      clearTimeout(timeoutId);
      
      if (response.ok) {
        return { success: true, message: `Connected to ${provider}` };
      }

      const errorText = await response.text();
      return parseLlmErrorResponse(response, errorText, provider);
    } finally {
      clearTimeout(timeoutId);
    }
  } catch (error) {
    console.error('Error testing LLM:', error);
    const errorMap = {
      AbortError: 'Connection timeout',
      ECONNREFUSED: 'Connection refused - check if service is running',
      ENOTFOUND: 'Host not found - check endpoint URL'
    };
    return { success: false, error: errorMap[error.name] || errorMap[error.code] || error.message };
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
  
  const bundledPath = bundledAgentDir.replaceAll('\\', '/');
  const writablePath = writableAgentDir.replaceAll('\\', '/');
  
  // Create a modified docker-compose.yml in the writable directory
  const composeContent = `services:
  agent:
    build: "${bundledPath}"
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
      - "${writablePath}/data:/app/data"
      
      # Mount writable output directory
      - "${writablePath}/output:/app/output"
      
      # Mount writable logs directory
      - "${writablePath}/logs:/app/logs"
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
    console.error('Docker check failed:', error);
    return { available: false, error: 'Docker Desktop is not installed or not running' };
  }
});

// IPC: Start Docker services
ipcMain.handle('docker:start', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout } = await execAsync(
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
    
    const { stdout } = await execAsync(
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
          console.error('Failed to parse docker status line:', e);
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
    const devUrl = "http://localhost:5175";
    console.log(`Loading dev URL: ${devUrl}`);
    win.loadURL(devUrl).catch(err => {
      console.error('Failed to load URL:', err);
      console.error('Make sure Vite dev server is running on port 5175');
    });
  }

  // Open DevTools in development
  if (!app.isPackaged) {
    win.webContents.openDevTools();
  }

  // Log when window is ready
  win.webContents.on('did-finish-load', () => {
    console.log('Window loaded successfully');
  });

  win.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('Failed to load window:', errorCode, errorDescription);
  });
}

app.whenReady().then(() => {
  console.log('Electron app is ready');
  
  // Initialize trigger watcher after app is ready (so app.getPath works)
  setupTriggerWatcher();
  
  createWindow();
  
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}).catch(err => {
  console.error('Error during app initialization:', err);
  process.exit(1);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});