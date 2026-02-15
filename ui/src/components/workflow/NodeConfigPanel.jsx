import React, { useState, useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import { localTimeToCron, cronToLocalTime } from '../../utils/cronUtils';

const NodeConfigPanel = ({ node, workflowName, onUpdate, onClose }) => {
  const [config, setConfig] = useState(node?.data || {});
  const fileInputRef = useRef(null);
  const [timeInput, setTimeInput] = useState(() => {
    // Initialize time from startTime first (most reliable), then cronExpression, or default to current time
    if (node?.data?.startTime) {
      return node.data.startTime;
    }
    if (node?.data?.cronExpression) {
      return cronToLocalTime(node.data.cronExpression);
    }
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  });

  // Sync config ONLY when switching to a different node (by ID change)
  // Do NOT sync on every node.data change to prevent resetting during editing
  useEffect(() => {
    if (node?.data) {
      setConfig(node.data);
      
      // Also update timeInput to reflect the latest schedule
      if (node.data.startTime) {
        setTimeInput(node.data.startTime);
      } else if (node.data.cronExpression) {
        setTimeInput(cronToLocalTime(node.data.cronExpression));
      }
    }
  }, [node?.id]); // ONLY depend on node ID, not node.data

  if (!node) return null;

  const handleConfigChange = (key, value) => {
    const newConfig = { ...config, [key]: value };
    setConfig(newConfig);
    onUpdate(node.id, newConfig);
  };

  const handleKeyDown = (e) => {
    // Prevent keydown events from propagating to the parent Flow component
    // This prevents accidental node deletion when typing in input fields
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.stopPropagation();
    }
  };

  const renderConfigFields = () => {
    switch (node.type) {
      case 'agent':
        return (
          <>
            <div className="config-field">
              <label htmlFor="agent-name">Agent Name</label>
              <input
                id="agent-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter agent name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="agent-description">Description</label>
              <textarea
                id="agent-description"
                value={config.description || ''}
                onChange={(e) => handleConfigChange('description', e.target.value)}
                placeholder="Describe the agent's purpose"
                rows={3}
              />
            </div>
            <div className="config-field">
              <label htmlFor="agent-instructions">Instructions</label>
              <textarea
                id="agent-instructions"
                value={config.instructions || ''}
                onChange={(e) => handleConfigChange('instructions', e.target.value)}
                placeholder="Agent instructions and behavior"
                rows={4}
              />
            </div>
          </>
        );
      
      case 'llm':
        return null; // LLM nodes are configured in Settings page only
      
      case 'database':
        return (
          <>
            <div className="config-field">
              <label htmlFor="db-type">Database Type</label>
              <select
                id="db-type"
                value={config.type || 'PostgreSQL'}
                onChange={(e) => handleConfigChange('type', e.target.value)}
              >
                <option value="PostgreSQL">PostgreSQL</option>
                <option value="MongoDB">MongoDB</option>
                <option value="MySQL">MySQL</option>
                <option value="Redis">Redis</option>
              </select>
            </div>
            <div className="config-field">
              <label htmlFor="db-connection">Connection String</label>
              <input
                id="db-connection"
                type="password"
                value={config.connectionString || ''}
                onChange={(e) => handleConfigChange('connectionString', e.target.value)}
                placeholder="Database connection string"
              />
            </div>
            <div className="config-field">
              <label htmlFor="db-name">Database Name</label>
              <input
                id="db-name"
                type="text"
                value={config.database || ''}
                onChange={(e) => handleConfigChange('database', e.target.value)}
                placeholder="Database name"
              />
            </div>
          </>
        );
    
      case 'teams':
        return (
          <>
            <div className="config-field">
              <label htmlFor="team-name">Team Name</label>
              <input
                id="team-name"
                type="text"
                value={config.team || ''}
                onChange={(e) => handleConfigChange('team', e.target.value)}
                placeholder="Team name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="team-channel">Channel</label>
              <input
                id="team-channel"
                type="text"
                value={config.channel || ''}
                onChange={(e) => handleConfigChange('channel', e.target.value)}
                placeholder="Channel name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="team-webhook">Webhook URL</label>
              <input
                id="team-webhook"
                type="url"
                value={config.webhookUrl || ''}
                onChange={(e) => handleConfigChange('webhookUrl', e.target.value)}
                placeholder="Teams webhook URL"
              />
            </div>
          </>
        );
      
      case 'chat':
        return (
          <>
            <div className="config-field">
              <label htmlFor="chat-name">Chat Interface Name</label>
              <input
                id="chat-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter chat interface name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="chat-welcome">Welcome Message</label>
              <textarea
                id="chat-welcome"
                value={config.welcomeMessage || ''}
                onChange={(e) => handleConfigChange('welcomeMessage', e.target.value)}
                placeholder="Custom welcome message for users"
                rows={2}
              />
            </div>
            <div className="config-field">
              <label htmlFor="chat-auto-response">Auto-Response</label>
              <div className="checkbox-group">
                <label htmlFor="chat-auto-response-checkbox">
                  <input
                    id="chat-auto-response-checkbox"
                    type="checkbox"
                    checked={config.autoResponse || false}
                    onChange={(e) => handleConfigChange('autoResponse', e.target.checked)}
                  />
                  {' '}Enable automatic responses
                </label>
              </div>
            </div>
            <div className="config-field">
              <label htmlFor="chat-history-limit">Message History Limit</label>
              <input
                id="chat-history-limit"
                type="number"
                value={config.historyLimit || 50}
                onChange={(e) => handleConfigChange('historyLimit', Number.parseInt(e.target.value, 10))}
                placeholder="Max messages to store"
                min="10"
                max="1000"
              />
            </div>
          </>
        );
      
      case 'orchestrator':
        return (
          <div className="config-field">
            <label htmlFor="orchestrator-file">SQL File / Workflow</label>
            <input 
              id="orchestrator-file"
              ref={fileInputRef}
              type="file" 
              accept=".sql,.json" 
              onChange={async (e) => {
                const file = e.target.files[0];
                if (file) {
                  try {
                    const fileName = file.name;
                    const fileType = fileName.endsWith('.sql') ? 'sql' : 'json';
                    const content = await file.text();
                    
                    // Store file content inline - will be uploaded when workflow is saved
                    const newConfig = {
                      ...config,
                      sqlFile: fileName,
                      fileName: fileName,
                      fileType: fileType,
                      fileContent: content,
                      pendingUpload: true  // Flag to indicate this needs to be uploaded
                    };
                    setConfig(newConfig);
                    onUpdate(node.id, newConfig);
                    console.log('SQL file loaded, will be uploaded when workflow is saved');
                  } catch (error) {
                    alert('Error reading file: ' + error.message);
                  }
                }
              }}
            />
            {config.fileName && (
              <div style={{ 
                marginTop: '8px', 
                padding: '8px', 
                background: '#f0f0f0', 
                borderRadius: '4px',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}>
                <span>📄 {config.fileName}</span>
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    // Reset file input
                    if (fileInputRef.current) {
                      fileInputRef.current.value = '';
                    }
                    const newConfig = {
                      ...config,
                      fileContent: '',
                      sqlFile: '',
                      fileName: '',
                      fileType: '',
                      stepCount: null
                    };
                    setConfig(newConfig);
                    onUpdate(node.id, newConfig);
                  }}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#ff4444',
                    cursor: 'pointer',
                    fontSize: '18px',
                    padding: '0 4px',
                    lineHeight: 1
                  }}
                  title="Remove file"
                >
                  ×
                </button>
              </div>
            )}
          </div>
        );
      
      case 'scheduler':
        return (
          <>
            <div className="config-field">
              <label htmlFor="scheduler-name">Schedule Name</label>
              <input
                id="scheduler-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter schedule name"
              />
            </div>
            
            <div className="config-field">
              <label htmlFor="scheduler-recurrence">Recurrence</label>
              <select
                id="scheduler-recurrence"
                value={config.recurrence || 'daily'}
                onChange={(e) => {
                  const recurrence = e.target.value;
                  
                  // Use utility function to generate cron from local time
                  const cronExpression = localTimeToCron(timeInput, recurrence);
                  
                  // Update recurrence, cron (UTC), AND preserve startTime (local)
                  const newConfig = {
                    ...config,
                    recurrence: recurrence,
                    cronExpression: cronExpression,
                    startTime: timeInput
                  };
                  setConfig(newConfig);
                  onUpdate(node.id, newConfig);
                }}
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
            
            <div className="config-field">
              <label htmlFor="scheduler-time">Time</label>
              <input
                id="scheduler-time"
                type="time"
                value={timeInput}
                onChange={(e) => {
                  const newTime = e.target.value;
                  setTimeInput(newTime);
                  
                  // Use utility function to generate cron from local time
                  const recurrence = config.recurrence || 'daily';
                  const cronExpression = localTimeToCron(newTime, recurrence);
                  
                  // Update both cronExpression (UTC) and startTime (local for display)
                  const newConfig = {
                    ...config,
                    cronExpression: cronExpression,
                    startTime: newTime
                  };
                  setConfig(newConfig);
                  onUpdate(node.id, newConfig);
                }}
              />
            </div>
          </>
        );
      
      default:
        return <div>No configuration available for this node type.</div>;
    }
  };

  return (
    <div 
      className="config-panel-overlay" 
      onClick={onClose}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      tabIndex={-1}
      aria-hidden="true"
    >
      <div 
        className="config-panel"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="config-header">
          <h3 id="config-panel-title">Configure {node.type.charAt(0).toUpperCase() + node.type.slice(1)} Node</h3>
          <button className="close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="config-content">
          {renderConfigFields()}
        </div>
        <div className="config-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={onClose}>Save</button>
        </div>
      </div>
    </div>
  );
};

NodeConfigPanel.propTypes = {
  node: PropTypes.shape({
    id: PropTypes.string,
    type: PropTypes.string,
    data: PropTypes.object,
  }),
  workflowName: PropTypes.string,
  onUpdate: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

NodeConfigPanel.defaultProps = {
  node: null,
  workflowName: '',
};

export default NodeConfigPanel;