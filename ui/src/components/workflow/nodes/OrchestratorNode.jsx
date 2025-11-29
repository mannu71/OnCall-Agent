import React from 'react';
import { Handle, Position } from 'reactflow';

const OrchestratorNode = ({ data, id }) => {
  return (
    <div className="orchestrator-node">

      {/* No left input handle */}

      <div className="node-header">
        <span className="node-icon">🧩</span>
        <span className="node-title">Orchestrator</span>
      </div>

      <div className="node-content">

        {/* Existing File Section */}
        <div className="orchestrator-info">
          {data.fileName ? (
            <>
              <div className="file-indicator">
                <span className="file-icon">📄</span>
                <span className="file-name">{data.fileName}</span>
              </div>

              {data.stepCount && (
                <div className="step-count">
                  {data.stepCount} step{data.stepCount !== 1 ? 's' : ''}
                </div>
              )}
            </>
          ) : (
            <div className="no-file-message">
              <span>Upload SQL file or JSON workflow</span>
            </div>
          )}
        </div>

        {/* Variables */}
        {data.vars && Object.keys(JSON.parse(data.vars || '{}')).length > 0 && (
          <div className="vars-indicator">
            <span className="vars-icon">⚙️</span>
            <span className="vars-count">
              {Object.keys(JSON.parse(data.vars)).length} variable
              {Object.keys(JSON.parse(data.vars)).length !== 1 ? 's' : ''}
            </span>
          </div>
        )}
      </div>

      {/* Status */}
      <div className="node-status">
        <span className={`status-indicator ${data.status || 'idle'}`}>
          {data.status === 'running' && '⏳'}
          {data.status === 'complete' && '✓'}
          {data.status === 'error' && '❌'}
          {!data.status && '⚪'}
        </span>
        <span className="status-text">
          {data.status === 'running' && 'Running'}
          {data.status === 'complete' && 'Complete'}
          {data.status === 'error' && 'Error'}
          {!data.status && 'Ready'}
        </span>
      </div>

      {/* Tool Input Handle - Bottom */}
      <Handle 
        type="target" 
        position={Position.Bottom} 
        id="tool"
        style={{ background: '#4caf50', left: '75%' }}
        title="Tool"
      />
      <div className="connection-label tool-label">Tool</div>

      {/* Output */}
      <Handle 
        type="source" 
        position={Position.Right} 
        id="orchestrator-output"
        style={{ background: '#555' }}
      />
    </div>
  );
};

export default OrchestratorNode;
