import React, { useState, useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import { localTimeToCron, cronToLocalTime } from '../../utils/cronUtils';
import { agentApiClient } from '../../services/agentApiClient';

const NodeConfigPanel = ({ node, workflowName, onUpdate, onClose }) => {
  const [config, setConfig] = useState(node?.data || {});
  const fileInputRef = useRef(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState(null);
  const [discoveringGroups, setDiscoveringGroups] = useState(false);
  const [discoveredGroups, setDiscoveredGroups] = useState([]);
  const [discoverPrefix, setDiscoverPrefix] = useState('');
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

  const handleDiscoverGroups = async () => {
    setDiscoveringGroups(true);
    setDiscoveredGroups([]);
    try {
      const result = await agentApiClient.discoverCloudWatchLogGroups(
        discoverPrefix || undefined,
        config.awsRegion || 'us-east-1'
      );
      setDiscoveredGroups((result.log_groups || []).map(g => g.name).filter(Boolean));
    } catch (error) {
      setDiscoveredGroups([]);
    } finally {
      setDiscoveringGroups(false);
    }
  };

  const handleAddDiscoveredGroup = (groupName) => {
    const existing = config.logGroups || [];
    if (!existing.includes(groupName)) {
      handleConfigChange('logGroups', [...existing, groupName]);
    }
    setDiscoveredGroups([]);
    setDiscoverPrefix('');
  };

  const handleTestConnection = async () => {
    setTestingConnection(true);
    setConnectionStatus(null);

    try {
      const result = await agentApiClient.testCloudWatchConnection(
        config.awsRegion || 'us-east-1',
        {
          awsProfile: config.awsProfile
        }
      );
      setConnectionStatus(result);
    } catch (error) {
      setConnectionStatus({
        success: false,
        message: `Connection failed: ${error.message}`
      });
    } finally {
      setTestingConnection(false);
    }
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
              <label>Agent Mode</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '8px' }}>
                <span style={{ fontSize: '13px', color: config.agentMode === 'multi' ? '#666' : '#1976d2', fontWeight: config.agentMode !== 'multi' ? '600' : 'normal' }}>
                  Single
                </span>
                <div
                  className="toggle-switch"
                  onClick={() => handleConfigChange('agentMode', config.agentMode === 'multi' ? 'single' : 'multi')}
                  style={{
                    position: 'relative',
                    width: '48px',
                    height: '24px',
                    backgroundColor: config.agentMode === 'multi' ? '#1976d2' : '#ccc',
                    borderRadius: '12px',
                    cursor: 'pointer',
                    transition: 'background-color 0.2s'
                  }}
                >
                  <div
                    style={{
                      position: 'absolute',
                      top: '2px',
                      left: config.agentMode === 'multi' ? '26px' : '2px',
                      width: '20px',
                      height: '20px',
                      backgroundColor: 'white',
                      borderRadius: '50%',
                      transition: 'left 0.2s',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.3)'
                    }}
                  />
                </div>
                <span style={{ fontSize: '13px', color: config.agentMode === 'multi' ? '#1976d2' : '#666', fontWeight: config.agentMode === 'multi' ? '600' : 'normal' }}>
                  Multi
                </span>
              </div>
              <small style={{ color: '#666', marginTop: '8px', display: 'block' }}>
                {config.agentMode === 'multi'
                  ? '🧩 Uses orchestrator to coordinate multiple specialized agents'
                  : '🤖 One agent handles all tasks'}
              </small>
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

      case 'cloudwatchAnalyzer':
        return (
          <>
            <div className="config-field">
              <label htmlFor="analyzer-name">Analyzer Name</label>
              <input
                id="analyzer-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter analyzer name"
              />
            </div>

            <div className="config-field">
              <label htmlFor="aws-region">AWS Region</label>
              <input
                id="aws-region"
                type="text"
                value={config.awsRegion || ''}
                onChange={(e) => handleConfigChange('awsRegion', e.target.value)}
                placeholder="us-east-1"
              />
            </div>

            <div className="config-field">
              <label htmlFor="aws-profile">AWS Profile</label>
              <input
                id="aws-profile"
                type="text"
                value={config.awsProfile || ''}
                onChange={(e) => handleConfigChange('awsProfile', e.target.value)}
                placeholder="default"
              />
              <small style={{ color: '#666', fontSize: '11px' }}>
                AWS profile name from ~/.aws/credentials
              </small>
            </div>

            <div className="config-field">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={testingConnection}
                style={{
                  background: testingConnection ? '#9e9e9e' : '#2196f3',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: testingConnection ? 'not-allowed' : 'pointer',
                  padding: '10px 16px',
                  width: '100%',
                  fontSize: '13px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px'
                }}
              >
                {testingConnection ? 'Testing...' : 'Test Connection'}
              </button>
              {connectionStatus && (
                <div style={{
                  marginTop: '10px',
                  padding: '10px',
                  borderRadius: '4px',
                  background: connectionStatus.success ? '#e8f5e9' : '#ffebee',
                  border: `1px solid ${connectionStatus.success ? '#4caf50' : '#f44336'}`,
                  fontSize: '12px'
                }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    color: connectionStatus.success ? '#2e7d32' : '#c62828'
                  }}>
                    <span style={{ fontSize: '16px' }}>
                      {connectionStatus.success ? '✓' : '✕'}
                    </span>
                    <span>{connectionStatus.message}</span>
                  </div>
                </div>
              )}
            </div>

            <div className="config-field">
              <label>Log Groups</label>
              <div className="log-groups-list" style={{ maxHeight: '200px', overflowY: 'auto', marginBottom: '10px' }}>
                {(config.logGroups || []).map((group, index) => (
                  <div key={index} className="log-group-item" style={{ display: 'flex', gap: '5px', marginBottom: '5px' }}>
                    <input
                      type="text"
                      value={group}
                      onChange={(e) => {
                        const newGroups = [...(config.logGroups || [])];
                        newGroups[index] = e.target.value;
                        handleConfigChange('logGroups', newGroups);
                      }}
                      placeholder="/aws/lambda/my-function"
                      style={{ flex: 1 }}
                    />
                    <button
                      className="remove-btn"
                      onClick={() => {
                        const newGroups = config.logGroups.filter((_, i) => i !== index);
                        handleConfigChange('logGroups', newGroups);
                      }}
                      style={{
                        background: '#f44336',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        padding: '4px 8px'
                      }}
                    >✕</button>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
                <button
                  className="add-btn"
                  onClick={() => handleConfigChange('logGroups', [...(config.logGroups || []), ''])}
                  style={{
                    background: '#4caf50',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    padding: '8px 12px',
                    flex: 1,
                  }}
                >+ Add</button>
                <button
                  type="button"
                  onClick={handleDiscoverGroups}
                  disabled={discoveringGroups}
                  style={{
                    background: discoveringGroups ? '#9e9e9e' : '#7b1fa2',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: discoveringGroups ? 'not-allowed' : 'pointer',
                    padding: '8px 12px',
                    flex: 1,
                    fontSize: '12px',
                  }}
                >
                  {discoveringGroups ? 'Discovering…' : '🔍 Discover'}
                </button>
              </div>

              {/* Discover prefix input */}
              <input
                type="text"
                value={discoverPrefix}
                onChange={(e) => setDiscoverPrefix(e.target.value)}
                placeholder="Prefix to discover, e.g. /aws/lambda/kyc-"
                style={{ marginTop: '6px', width: '100%', fontSize: '12px', padding: '4px 6px', boxSizing: 'border-box' }}
              />

              {/* Discovered groups dropdown */}
              {discoveredGroups.length > 0 && (
                <div style={{
                  marginTop: '6px',
                  border: '1px solid #ccc',
                  borderRadius: '4px',
                  maxHeight: '140px',
                  overflowY: 'auto',
                  background: '#fff',
                }}>
                  <div style={{ padding: '4px 8px', fontSize: '11px', color: '#666', borderBottom: '1px solid #eee' }}>
                    Click to add to Log Groups
                  </div>
                  {discoveredGroups.map((g) => (
                    <div
                      key={g}
                      onClick={() => handleAddDiscoveredGroup(g)}
                      style={{
                        padding: '5px 8px',
                        cursor: 'pointer',
                        fontSize: '12px',
                        borderBottom: '1px solid #f5f5f5',
                      }}
                      onMouseOver={(e) => { e.currentTarget.style.background = '#e3f2fd'; }}
                      onMouseOut={(e) => { e.currentTarget.style.background = ''; }}
                    >
                      {g}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="config-field">
              <label htmlFor="analysis-type">Analysis Type</label>
              <select
                id="analysis-type"
                value={config.analysisType || 'error-patterns'}
                onChange={(e) => handleConfigChange('analysisType', e.target.value)}
              >
                <option value="error-patterns">Error Patterns</option>
                <option value="activity-summary">Activity Summary</option>
                <option value="anomaly-detection">Anomaly Detection</option>
                <option value="correlation">Cross-Group Correlation</option>
                <option value="metrics">CloudWatch Metrics</option>
                <option value="alarms">CloudWatch Alarms</option>
                <option value="custom-query">Custom Insights Query</option>
              </select>
            </div>

            {/* Custom query textarea — shown only for custom-query type */}
            {config.analysisType === 'custom-query' && (
              <div className="config-field">
                <label htmlFor="custom-insights-query">Insights Query</label>
                <textarea
                  id="custom-insights-query"
                  rows={5}
                  value={config.customInsightsQuery || ''}
                  onChange={(e) => handleConfigChange('customInsightsQuery', e.target.value)}
                  placeholder={`fields @timestamp, @message\n| filter @message like /error/\n| sort @timestamp desc\n| limit 100`}
                  style={{ width: '100%', fontFamily: 'monospace', fontSize: '12px', resize: 'vertical', boxSizing: 'border-box' }}
                />
                <small style={{ color: '#666', fontSize: '11px' }}>CloudWatch Logs Insights query syntax</small>
              </div>
            )}

            {/* Alarm filter — shown only for alarms type */}
            {config.analysisType === 'alarms' && (
              <div className="config-field">
                <label htmlFor="alarm-state-filter">Alarm State Filter</label>
                <select
                  id="alarm-state-filter"
                  value={config.alarmStateFilter || ''}
                  onChange={(e) => handleConfigChange('alarmStateFilter', e.target.value)}
                >
                  <option value="">All states</option>
                  <option value="ALARM">ALARM (firing)</option>
                  <option value="OK">OK</option>
                  <option value="INSUFFICIENT_DATA">Insufficient Data</option>
                </select>
              </div>
            )}

            <div className="config-field">
              <label htmlFor="time-range">Time Range</label>
              <select
                id="time-range"
                value={config.timeRange || '1h'}
                onChange={(e) => handleConfigChange('timeRange', e.target.value)}
              >
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last 1 hour</option>
                <option value="6h">Last 6 hours</option>
                <option value="24h">Last 24 hours</option>
                <option value="7d">Last 7 days</option>
              </select>
            </div>

            <div className="config-field">
              <label htmlFor="error-threshold">Error Threshold</label>
              <input
                id="error-threshold"
                type="number"
                value={config.errorThreshold || 10}
                onChange={(e) => handleConfigChange('errorThreshold', parseInt(e.target.value))}
                placeholder="10"
                min="1"
              />
              <small style={{ color: '#666', fontSize: '11px' }}>Alert if error count exceeds this value</small>
            </div>

            <div className="config-field checkbox-field">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={config.enableAlerts || false}
                  onChange={(e) => handleConfigChange('enableAlerts', e.target.checked)}
                />
                <span>Enable Alerts</span>
              </label>
            </div>
          </>
        );

      case 'codeAnalyzer':
        return (
          <CodeAnalyzerConfig config={config} handleConfigChange={handleConfigChange} />
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

// ─────────────────────────────────────────────────────────────────────────────
// Code Analyzer config sub-component
// ─────────────────────────────────────────────────────────────────────────────

function validateRepoPath(path) {
  if (!path) return 'Path is required';
  if (!path.startsWith('/') && !/^[A-Za-z]:[/\\]/.test(path))
    return 'Must be an absolute path (starts with / or C:\\)';
  if (path.includes('..'))
    return 'Path must not contain ".." traversal segments';
  return null;
}

function validateRepoName(name) {
  if (!name) return 'Name is required';
  if (!/^[a-z0-9][a-z0-9\-_]*$/.test(name))
    return 'Name must be lowercase alphanumeric with hyphens/underscores';
  return null;
}

function CodeAnalyzerConfig({ config, handleConfigChange }) {
  const repos = config.repos || [];
  const [fieldErrors, setFieldErrors] = useState({});

  const updateRepo = (index, field, value) => {
    const updated = repos.map((r, i) => (i === index ? { ...r, [field]: value } : r));
    handleConfigChange('repos', updated);
  };

  const addRepo = () => {
    handleConfigChange('repos', [
      ...repos,
      { name: '', path: '', language: 'python' },
    ]);
  };

  const removeRepo = (index) => {
    handleConfigChange('repos', repos.filter((_, i) => i !== index));
  };

  const handleBlur = (key, value) => {
    const err = key === 'path' ? validateRepoPath(value) : validateRepoName(value);
    setFieldErrors((prev) => ({ ...prev, [key]: err }));
  };

  const hasPathWarning = repos.some((r) => validateRepoPath(r.path));

  return (
    <>
      <div className="config-field">
        <label htmlFor="ca-label">Label</label>
        <input
          id="ca-label"
          type="text"
          value={config.label || ''}
          onChange={(e) => handleConfigChange('label', e.target.value)}
          placeholder="Code Analyzer"
        />
      </div>

      <div className="config-field">
        <label>
          Repositories
          {hasPathWarning && (
            <span style={{ marginLeft: 8, color: '#f57c00', fontSize: 11 }}>
              ⚠ one or more repos have path issues
            </span>
          )}
        </label>
        {repos.map((repo, idx) => (
          <div
            key={idx}
            style={{
              border: '1px solid #e0e0e0',
              borderRadius: 6,
              padding: '10px 12px',
              marginBottom: 8,
              background: '#fafafa',
            }}
          >
            {/* Row 1: name + language + delete */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <input
                  type="text"
                  value={repo.name || ''}
                  onChange={(e) => updateRepo(idx, 'name', e.target.value)}
                  onBlur={(e) => handleBlur(`name-${idx}`, e.target.value)}
                  placeholder="repo-name"
                  style={{
                    width: '100%',
                    fontSize: 12,
                    padding: '4px 8px',
                    border: '1px solid #d0d0d0',
                    borderRadius: 4,
                    background: '#fff',
                    boxSizing: 'border-box',
                  }}
                />
                {fieldErrors[`name-${idx}`] && (
                  <span style={{ color: '#d32f2f', fontSize: 10 }}>
                    {fieldErrors[`name-${idx}`]}
                  </span>
                )}
              </div>
              <select
                value={repo.language || 'python'}
                onChange={(e) => updateRepo(idx, 'language', e.target.value)}
                style={{
                  fontSize: 12,
                  width: 130,
                  flexShrink: 0,
                  padding: '4px 6px',
                  border: '1px solid #d0d0d0',
                  borderRadius: 4,
                  background: '#fff',
                }}
              >
                <option value="python">Python</option>
                <option value="typescript">TypeScript</option>
                <option value="react">React / JSX</option>
                <option value="csharp">C# / .NET</option>
                <option value="mixed">Mixed (Py + TS)</option>
              </select>
              <button
                type="button"
                onClick={() => removeRepo(idx)}
                style={{
                  flexShrink: 0,
                  background: '#ffebee',
                  border: '1px solid #ef9a9a',
                  borderRadius: 4,
                  cursor: 'pointer',
                  color: '#c62828',
                  padding: '4px 10px',
                  fontSize: 12,
                }}
              >
                ✕
              </button>
            </div>
            {/* Row 2: path */}
            <div>
              <input
                type="text"
                value={repo.path || ''}
                onChange={(e) => updateRepo(idx, 'path', e.target.value)}
                onBlur={(e) => handleBlur(`path-${idx}`, e.target.value)}
                placeholder="/absolute/path/to/repo (as seen by server)"
                style={{
                  width: '100%',
                  fontSize: 12,
                  padding: '4px 8px',
                  border: '1px solid #d0d0d0',
                  borderRadius: 4,
                  background: '#fff',
                  boxSizing: 'border-box',
                }}
              />
              {fieldErrors[`path-${idx}`] && (
                <span style={{ color: '#d32f2f', fontSize: 10 }}>
                  {fieldErrors[`path-${idx}`]}
                </span>
              )}
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={addRepo}
          style={{
            background: '#e8f5e9',
            border: '1px solid #a5d6a7',
            borderRadius: 4,
            cursor: 'pointer',
            color: '#2e7d32',
            padding: '6px 12px',
            fontSize: 12,
            width: '100%',
          }}
        >
          + Add Repository
        </button>
        <small style={{ color: '#666', fontSize: 11 }}>
          Use the path as seen by the server (container path if running in Docker).
        </small>
      </div>

      <div className="config-field checkbox-field">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.autoIndex !== false}
            onChange={(e) => handleConfigChange('autoIndex', e.target.checked)}
          />
          <span>Auto-Index (index on first use and when stale)</span>
        </label>
      </div>

      {config.autoIndex !== false && (
        <div className="config-field">
          <label htmlFor="ca-stale-hours">Re-index after (hours)</label>
          <input
            id="ca-stale-hours"
            type="number"
            value={config.staleAfterHours || 24}
            onChange={(e) =>
              handleConfigChange('staleAfterHours', parseInt(e.target.value, 10))
            }
            min={1}
            style={{ width: 80 }}
          />
        </div>
      )}

      <div className="config-field checkbox-field">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.preSummary || false}
            onChange={(e) => handleConfigChange('preSummary', e.target.checked)}
          />
          <span>Pre-Execution Summary</span>
        </label>
        <small style={{ color: '#666', fontSize: 11 }}>
          Runs a broad code search before the agent starts and injects a summary
          (minimal tokens).
        </small>
      </div>
    </>
  );
}

CodeAnalyzerConfig.propTypes = {
  config: PropTypes.object.isRequired,
  handleConfigChange: PropTypes.func.isRequired,
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