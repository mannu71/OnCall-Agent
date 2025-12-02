import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { app } from 'electron';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Determine base path for agent files
// In packaged app, use a writable folder in user's app data
// In dev, agent files are in ../agent from ui folder
const getAgentPath = () => {
  if (app.isPackaged) {
    // Use app data folder for writable storage
    // This creates: C:\Users\<user>\AppData\Roaming\oncall-agent\agent
    return path.join(app.getPath('userData'), 'agent');
  } else {
    // In development, agent is sibling to ui folder
    return path.join(__dirname, '..', '..', 'agent');
  }
};

// Get the bundled resources path (for initial config copy)
const getBundledAgentPath = () => {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'agent');
  }
  return null;
};

const agentDir = getAgentPath();
const bundledAgentDir = getBundledAgentPath();
const configDir = path.join(agentDir, 'data', 'config');
const schedulesPath = path.join(configDir, 'schedules.json');
const workflowsPath = path.join(configDir, 'workflows.json');
const mcpConfigPath = path.join(configDir, 'mcp-servers.json');
const llmConfigPath = path.join(configDir, 'llm-config.json');
const sqlFilesDir = path.join(configDir, 'sql');
const triggersDir = path.join(configDir, 'triggers');
const outputDir = path.join(agentDir, 'output');

console.log('fileUtils: app.isPackaged =', app.isPackaged);
console.log('fileUtils: agentDir =', agentDir);
console.log('fileUtils: bundledAgentDir =', bundledAgentDir);
console.log('fileUtils: configDir =', configDir);
console.log('fileUtils: mcpConfigPath =', mcpConfigPath);
console.log('fileUtils: triggersDir =', triggersDir);
console.log('fileUtils: outputDir =', outputDir);

// Initialize writable directories and copy bundled files if needed
function initializeDataDirectories() {
  try {
    // Create all necessary directories
    const dirs = [configDir, sqlFilesDir, triggersDir, outputDir];
    dirs.forEach(dir => {
      if (!fs.existsSync(dir)) {
        console.log('fileUtils: creating directory:', dir);
        fs.mkdirSync(dir, { recursive: true });
      }
    });

    // In packaged app, copy bundled config files if they don't exist in writable location
    if (app.isPackaged && bundledAgentDir) {
      const bundledConfigDir = path.join(bundledAgentDir, 'data', 'config');
      
      // Copy schedules.json if not exists
      if (!fs.existsSync(schedulesPath) && fs.existsSync(path.join(bundledConfigDir, 'schedules.json'))) {
        fs.copyFileSync(path.join(bundledConfigDir, 'schedules.json'), schedulesPath);
        console.log('fileUtils: copied bundled schedules.json');
      }
      
      // Copy workflows.json if not exists
      if (!fs.existsSync(workflowsPath) && fs.existsSync(path.join(bundledConfigDir, 'workflows.json'))) {
        fs.copyFileSync(path.join(bundledConfigDir, 'workflows.json'), workflowsPath);
        console.log('fileUtils: copied bundled workflows.json');
      }
      
      // Copy mcp-servers.json if not exists
      if (!fs.existsSync(mcpConfigPath) && fs.existsSync(path.join(bundledConfigDir, 'mcp-servers.json'))) {
        fs.copyFileSync(path.join(bundledConfigDir, 'mcp-servers.json'), mcpConfigPath);
        console.log('fileUtils: copied bundled mcp-servers.json');
      }
      
      // Copy SQL files if not exists
      const bundledSqlDir = path.join(bundledConfigDir, 'sql');
      if (fs.existsSync(bundledSqlDir)) {
        const sqlFiles = fs.readdirSync(bundledSqlDir);
        sqlFiles.forEach(file => {
          const destPath = path.join(sqlFilesDir, file);
          if (!fs.existsSync(destPath)) {
            fs.copyFileSync(path.join(bundledSqlDir, file), destPath);
            console.log('fileUtils: copied bundled SQL file:', file);
          }
        });
      }
    }
    
    console.log('fileUtils: data directories initialized successfully');
    return true;
  } catch (error) {
    console.error('fileUtils: error initializing directories:', error);
    return false;
  }
}

// Initialize on module load
initializeDataDirectories();

// Function to save schedules to JSON file
function saveSchedulesToFile(schedules) {
  try {
    const dataDir = path.dirname(schedulesPath);
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }
    
    if (!Array.isArray(schedules)) {
      throw new Error('Schedules payload must be an array');
    }
    
    // Clean up trigger files for deleted schedules
    cleanupTriggers(schedules);
    
    fs.writeFileSync(schedulesPath, JSON.stringify(schedules, null, 2));
    return { success: true, path: schedulesPath };
  } catch (error) {
    console.error('Error saving schedules:', error);
    return { success: false, error: error.message };
  }
}

// Function to load schedules from JSON file
function loadSchedulesFromFile() {
  try {
    if (fs.existsSync(schedulesPath)) {
      const data = fs.readFileSync(schedulesPath, 'utf-8').trim();
      if (!data) {
        console.log('Schedules file is empty, returning empty array');
        return [];
      }
      const parsed = JSON.parse(data);
      return Array.isArray(parsed) ? parsed : [];
    }
    return [];
  } catch (error) {
    console.error('Error loading schedules:', error);
    return [];
  }
}

// Function to save multiple workflows to workflows.json
function saveWorkflowsToFile(workflows) {
  try {
    console.log('fileUtils: saveWorkflowsToFile called with:', workflows);
    console.log('fileUtils: workflowsPath is:', workflowsPath);
    
    const dataDir = path.dirname(workflowsPath);
    console.log('fileUtils: dataDir is:', dataDir);
    
    if (!fs.existsSync(dataDir)) {
      console.log('fileUtils: dataDir does not exist, creating it');
      fs.mkdirSync(dataDir, { recursive: true });
    }
    
    if (!Array.isArray(workflows)) {
      throw new Error('Workflows payload must be an array');
    }
    
    console.log('fileUtils: Writing workflows to file:', workflowsPath);
    fs.writeFileSync(workflowsPath, JSON.stringify(workflows, null, 2));
    console.log('fileUtils: Workflows file written successfully');
    
    return { success: true, path: workflowsPath };
  } catch (error) {
    console.error('Error saving workflows:', error);
    return { success: false, error: error.message };
  }
}

// Function to load multiple workflows from workflows.json
function loadWorkflowsFromFile() {
  try {
    console.log('fileUtils: Loading workflows from:', workflowsPath);
    console.log('fileUtils: File exists?', fs.existsSync(workflowsPath));
    if (fs.existsSync(workflowsPath)) {
      const data = fs.readFileSync(workflowsPath, 'utf-8').trim();
      if (!data) {
        console.log('Workflows file is empty, returning empty array');
        return [];
      }
      const parsed = JSON.parse(data);
      console.log('fileUtils: Loaded workflows count:', Array.isArray(parsed) ? parsed.length : 0);
      return Array.isArray(parsed) ? parsed : [];
    }
    console.log('fileUtils: Workflows file does not exist, returning empty array');
    return [];
  } catch (error) {
    console.error('Error loading workflows:', error);
    return [];
  }
}

// Create trigger flag for a specific workflow
function triggerWorkflow(workflowName) {
  try {
    if (!fs.existsSync(triggersDir)) {
      fs.mkdirSync(triggersDir, { recursive: true });
    }
    const safeName = workflowName.replace(/\s+/g, '_');
    const triggerPath = path.join(triggersDir, `${safeName}.flag`);
    fs.writeFileSync(triggerPath, Date.now().toString());
    return { success: true, path: triggerPath, workflow: workflowName };
  } catch (error) {
    console.error('Error creating workflow trigger flag:', error);
    return { success: false, error: error.message };
  }
}

// Run an agent workflow with a user query
async function runAgentWorkflow(workflowName, userQuery) {
  try {
    const { spawn } = await import('child_process');
    
    return new Promise((resolve, reject) => {
      // Run the agent using node
      const agentPath = path.join(agentDir, 'src', 'agents', 'run.js');
      
      const child = spawn('node', [agentPath, workflowName, userQuery], {
        cwd: agentDir,
        env: { ...process.env },
        stdio: ['pipe', 'pipe', 'pipe']
      });
      
      let stdout = '';
      let stderr = '';
      
      child.stdout.on('data', (data) => {
        stdout += data.toString();
      });
      
      child.stderr.on('data', (data) => {
        stderr += data.toString();
      });
      
      child.on('close', (code) => {
        if (code === 0) {
          // Extract the final answer from the output
          const finalAnswerMatch = stdout.match(/🔥 FINAL ANSWER:\s*([\s\S]*)/);
          const answer = finalAnswerMatch ? finalAnswerMatch[1].trim() : stdout;
          
          resolve({ 
            success: true, 
            answer: answer,
            fullOutput: stdout
          });
        } else {
          resolve({ 
            success: false, 
            error: stderr || `Agent exited with code ${code}`,
            fullOutput: stdout + stderr
          });
        }
      });
      
      child.on('error', (err) => {
        resolve({ success: false, error: err.message });
      });
      
      // Timeout after 5 minutes
      setTimeout(() => {
        child.kill();
        resolve({ success: false, error: 'Agent execution timed out after 5 minutes' });
      }, 5 * 60 * 1000);
    });
  } catch (error) {
    console.error('Error running agent workflow:', error);
    return { success: false, error: error.message };
  }
}

// Clean up trigger files for deleted schedules
function cleanupTriggers(currentSchedules) {
  try {
    if (!fs.existsSync(triggersDir)) {
      return;
    }
    
    // Get all current workflow names
    const activeWorkflows = new Set(
      currentSchedules.map(sch => sch.workflow?.replace(/\s+/g, '_')).filter(Boolean)
    );
    
    // Read all trigger files
    const triggerFiles = fs.readdirSync(triggersDir).filter(f => f.endsWith('.flag'));
    
    // Delete triggers that don't have corresponding schedules
    triggerFiles.forEach(file => {
      const workflowName = file.replace('.flag', '');
      if (!activeWorkflows.has(workflowName)) {
        const triggerPath = path.join(triggersDir, file);
        try {
          fs.unlinkSync(triggerPath);
          console.log(`Deleted orphaned trigger: ${file}`);
        } catch (error) {
          console.error(`Error deleting trigger ${file}:`, error);
        }
      }
    });
  } catch (error) {
    console.error('Error cleaning up triggers:', error);
  }
}

// Function to save SQL file to config/sql/ directory
function saveSqlFile(filename, content) {
  try {
    if (!fs.existsSync(sqlFilesDir)) {
      fs.mkdirSync(sqlFilesDir, { recursive: true });
    }
    
    // Sanitize filename to prevent directory traversal
    const sanitizedFilename = path.basename(filename);
    const sqlFilePath = path.join(sqlFilesDir, sanitizedFilename);
    
    fs.writeFileSync(sqlFilePath, content, 'utf-8');
    console.log(`Saved SQL file: ${sqlFilePath}`);
    
    // Return relative path for storage in workflow JSON
    return { 
      success: true, 
      path: sqlFilePath,
      relativePath: `sql/${sanitizedFilename}`
    };
  } catch (error) {
    console.error('Error saving SQL file:', error);
    return { success: false, error: error.message };
  }
}

// Function to load workflow execution results from output directory
function loadWorkflowRuns() {
  try {
    console.log('loadWorkflowRuns: outputDir =', outputDir);
    if (!fs.existsSync(outputDir)) {
      console.log('Output directory does not exist');
      return [];
    }
    
    const files = fs.readdirSync(outputDir)
      .filter(f => f.endsWith('.json') && !f.endsWith('_latest.json'))
      .sort((a, b) => b.localeCompare(a)); // Sort by filename (timestamp) descending
    
    console.log('loadWorkflowRuns: found files =', files);
    
    const runs = files.slice(0, 10).map(file => {
      try {
        const filePath = path.join(outputDir, file);
        const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        
        // Extract timestamp from filename - format: daily-report_2025-11-26T08-41-42-838Z.json
        const timestampMatch = file.match(/(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z/);
        let timestamp = data.timestamp;
        if (timestampMatch) {
          // Reconstruct proper ISO timestamp: 2025-11-26T08:41:42.838Z
          timestamp = `${timestampMatch[1]}T${timestampMatch[2]}:${timestampMatch[3]}:${timestampMatch[4]}.${timestampMatch[5]}Z`;
        }
        
        return {
          workflow: data.workflow,
          timestamp: timestamp,
          success: data.success,
          duration: calculateDuration(data),
          resultCount: data.results ? data.results.length : 0,
          filename: file
        };
      } catch (error) {
        console.error(`Error reading workflow run file ${file}:`, error);
        return null;
      }
    }).filter(Boolean);
    
    console.log('loadWorkflowRuns: returning runs count =', runs.length);
    return runs;
  } catch (error) {
    console.error('Error loading workflow runs:', error);
    return [];
  }
}

// Helper function to calculate duration (placeholder - you may want to add actual timing data)
function calculateDuration(data) {
  // If you add start/end timestamps to your workflow results, calculate here
  // For now, return a placeholder
  return '~2s';
}

// Function to load the latest workflow result details
function loadLatestWorkflowResult() {
  try {
    console.log('loadLatestWorkflowResult: outputDir =', outputDir);
    if (!fs.existsSync(outputDir)) {
      console.log('Output directory does not exist');
      return null;
    }
    
    // Find any *_latest.json file
    const latestFiles = fs.readdirSync(outputDir)
      .filter(f => f.endsWith('_latest.json'));
    
    console.log('loadLatestWorkflowResult: found latest files =', latestFiles);
    
    if (latestFiles.length === 0) {
      console.log('No latest workflow result file found');
      return null;
    }
    
    // Get the most recently modified latest file
    let mostRecentFile = latestFiles[0];
    let mostRecentTime = 0;
    
    latestFiles.forEach(file => {
      const filePath = path.join(outputDir, file);
      const stats = fs.statSync(filePath);
      if (stats.mtimeMs > mostRecentTime) {
        mostRecentTime = stats.mtimeMs;
        mostRecentFile = file;
      }
    });
    
    const latestPath = path.join(outputDir, mostRecentFile);
    console.log('loadLatestWorkflowResult: loading from =', latestPath);
    
    const data = JSON.parse(fs.readFileSync(latestPath, 'utf-8'));
    return data;
  } catch (error) {
    console.error('Error loading latest workflow result:', error);
    return null;
  }
}

function loadWorkflowResult(filename) {
  try {
    const filePath = path.join(outputDir, filename);
    
    if (!fs.existsSync(filePath)) {
      console.log('Workflow result file does not exist:', filename);
      return null;
    }
    
    const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    return data;
  } catch (error) {
    console.error('Error loading workflow result:', error);
    return null;
  }
}

// Function to clear all workflow output files
function clearWorkflowOutputs() {
  try {
    console.log('clearWorkflowOutputs: outputDir =', outputDir);
    if (!fs.existsSync(outputDir)) {
      console.log('Output directory does not exist, nothing to clear');
      return { success: true, deletedCount: 0 };
    }
    
    const files = fs.readdirSync(outputDir)
      .filter(f => f.endsWith('.json'));
    
    let deletedCount = 0;
    files.forEach(file => {
      try {
        const filePath = path.join(outputDir, file);
        fs.unlinkSync(filePath);
        deletedCount++;
        console.log(`Deleted output file: ${file}`);
      } catch (error) {
        console.error(`Error deleting file ${file}:`, error);
      }
    });
    
    console.log(`clearWorkflowOutputs: deleted ${deletedCount} files`);
    return { success: true, deletedCount };
  } catch (error) {
    console.error('Error clearing workflow outputs:', error);
    return { success: false, error: error.message };
  }
}

// Function to load SQL file from config/sql/ directory
function loadSqlFile(relativePath) {
  try {
    const fullPath = path.join(configDir, relativePath);
    
    if (!fs.existsSync(fullPath)) {
      return { success: false, error: 'File not found' };
    }
    
    const content = fs.readFileSync(fullPath, 'utf-8');
    return { success: true, content };
  } catch (error) {
    console.error('Error loading SQL file:', error);
    return { success: false, error: error.message };
  }
}

// Function to save MCP servers configuration to JSON file
function saveMCPConfigToFile(config) {
  try {
    const dataDir = path.dirname(mcpConfigPath);
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }
    
    if (typeof config !== 'object' || config === null) {
      throw new Error('MCP config must be an object');
    }
    
    fs.writeFileSync(mcpConfigPath, JSON.stringify(config, null, 2));
    console.log('fileUtils: MCP config saved to:', mcpConfigPath);
    return { success: true, path: mcpConfigPath };
  } catch (error) {
    console.error('Error saving MCP config:', error);
    return { success: false, error: error.message };
  }
}

// Function to load MCP servers configuration from JSON file
function loadMCPConfigFromFile() {
  try {
    console.log('fileUtils: Loading MCP config from:', mcpConfigPath);
    if (fs.existsSync(mcpConfigPath)) {
      const data = fs.readFileSync(mcpConfigPath, 'utf-8').trim();
      if (!data) {
        console.log('MCP config file is empty, returning default');
        return { servers: {}, inputs: [] };
      }
      const parsed = JSON.parse(data);
      console.log('fileUtils: MCP config loaded successfully');
      return parsed;
    }
    console.log('fileUtils: MCP config file does not exist, returning default');
    return { servers: {}, inputs: [] };
  } catch (error) {
    console.error('Error loading MCP config:', error);
    return { servers: {}, inputs: [] };
  }
}

// Function to save LLM configuration to JSON file
function saveLLMConfigToFile(config) {
  try {
    const dataDir = path.dirname(llmConfigPath);
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }
    
    if (typeof config !== 'object' || config === null) {
      throw new Error('LLM config must be an object');
    }
    
    fs.writeFileSync(llmConfigPath, JSON.stringify(config, null, 2));
    console.log('fileUtils: LLM config saved to:', llmConfigPath);
    return { success: true, path: llmConfigPath };
  } catch (error) {
    console.error('Error saving LLM config:', error);
    return { success: false, error: error.message };
  }
}

// Function to load LLM configuration from JSON file
function loadLLMConfigFromFile() {
  try {
    console.log('fileUtils: Loading LLM config from:', llmConfigPath);
    if (fs.existsSync(llmConfigPath)) {
      const data = fs.readFileSync(llmConfigPath, 'utf-8').trim();
      if (!data) {
        console.log('LLM config file is empty, returning default');
        return { llms: {} };
      }
      const parsed = JSON.parse(data);
      console.log('fileUtils: LLM config loaded successfully');
      return parsed;
    }
    console.log('fileUtils: LLM config file does not exist, returning default');
    return { llms: {} };
  } catch (error) {
    console.error('Error loading LLM config:', error);
    return { llms: {} };
  }
}

// Function to get debug info about paths
function getPathsInfo() {
  return {
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    userDataPath: app.getPath('userData'),
    agentDir,
    configDir,
    triggersDir,
    outputDir,
    mcpConfigPath,
    triggersExists: fs.existsSync(triggersDir),
    outputExists: fs.existsSync(outputDir),
    mcpConfigExists: fs.existsSync(mcpConfigPath),
    triggerFiles: fs.existsSync(triggersDir) ? fs.readdirSync(triggersDir) : [],
    outputFiles: fs.existsSync(outputDir) ? fs.readdirSync(outputDir).filter(f => f.endsWith('.json')) : []
  };
}

// Function to set API key for an LLM (stores in llm-config.json)
function setLLMApiKey(llmName, apiKey) {
  try {
    const config = loadLLMConfigFromFile();
    if (!config.llms) {
      config.llms = {};
    }
    if (!config.llms[llmName]) {
      return { success: false, error: `LLM '${llmName}' not found` };
    }
    if (apiKey) {
      config.llms[llmName].apiKey = apiKey;
    } else {
      delete config.llms[llmName].apiKey;
    }
    return saveLLMConfigToFile(config);
  } catch (error) {
    console.error('Error setting LLM API key:', error);
    return { success: false, error: error.message };
  }
}

// Function to get masked API key for an LLM
function getLLMApiKeyMasked(llmName) {
  try {
    const config = loadLLMConfigFromFile();
    const key = config.llms?.[llmName]?.apiKey;
    if (!key) return null;
    // Return masked version: show first 4 and last 4 chars
    if (key.length <= 8) return '****';
    return key.substring(0, 4) + '****' + key.substring(key.length - 4);
  } catch (error) {
    console.error('Error getting LLM API key:', error);
    return null;
  }
}

// Function to check if an LLM has an API key
function hasLLMApiKey(llmName) {
  try {
    const config = loadLLMConfigFromFile();
    return !!config.llms?.[llmName]?.apiKey;
  } catch (error) {
    console.error('Error checking LLM API key:', error);
    return false;
  }
}

// Function to delete an LLM's API key
function deleteLLMApiKey(llmName) {
  return setLLMApiKey(llmName, null);
}

// Function to get actual API key for an LLM (for testing)
function getLLMApiKey(llmName) {
  try {
    const config = loadLLMConfigFromFile();
    return config.llms?.[llmName]?.apiKey || null;
  } catch (error) {
    console.error('Error getting LLM API key:', error);
    return null;
  }
}

export {
  saveSchedulesToFile,
  loadSchedulesFromFile,
  saveWorkflowsToFile,
  loadWorkflowsFromFile,
  saveMCPConfigToFile,
  loadMCPConfigFromFile,
  saveLLMConfigToFile,
  loadLLMConfigFromFile,
  setLLMApiKey,
  getLLMApiKeyMasked,
  getLLMApiKey,
  hasLLMApiKey,
  deleteLLMApiKey,
  triggerWorkflow,
  runAgentWorkflow,
  cleanupTriggers,
  saveSqlFile,
  loadSqlFile,
  loadWorkflowRuns,
  loadLatestWorkflowResult,
  loadWorkflowResult,
  clearWorkflowOutputs,
  getPathsInfo,
  schedulesPath,
  workflowsPath,
  mcpConfigPath,
  llmConfigPath,
  sqlFilesDir
};