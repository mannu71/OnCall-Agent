import React, { useState, useEffect, useRef } from 'react';
import { Handle, Position, useReactFlow } from 'reactflow';

const OutputNode = ({ data, id }) => {
  const [output, setOutput] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState('');
  const [connectedAgentId, setConnectedAgentId] = useState(null);
  const [dynamicHeight, setDynamicHeight] = useState(200);
  const viewportRef = useRef(null);
  
  const reactFlow = useReactFlow();

  // Check if properly connected to an agent
  useEffect(() => {
    try {
      const edges = reactFlow.getEdges();
      const nodes = reactFlow.getNodes();
      
      // Find edges connected to this output node
      const incomingEdges = edges.filter(edge => edge.target === id && edge.targetHandle === 'input');
      
      if (incomingEdges.length > 0) {
        const connectedEdge = incomingEdges[0]; // Should only be one connection
        const sourceNode = nodes.find(node => node.id === connectedEdge.source);
        
        if (sourceNode && sourceNode.type === 'agent' && connectedEdge.sourceHandle === 'agent-output') {
          setIsConnected(true);
          setConnectedAgentId(sourceNode.id);
        } else {
          setIsConnected(false);
          setConnectedAgentId(null);
        }
      } else {
        setIsConnected(false);
        setConnectedAgentId(null);
      }
    } catch (error) {
      console.warn('Error checking connections:', error);
      setIsConnected(false);
      setConnectedAgentId(null);
    }
  }, [id, reactFlow, data]); // Simplified dependencies

  // Listen for incoming data from connected agents
  useEffect(() => {
    if (data.input) {
      setOutput(data.input);
      setLastUpdated(data.lastUpdated || new Date().toISOString());
      
      // Calculate dynamic height based on content
      const calculateHeight = (content) => {
        const lines = content.split('\n').length;
        const avgCharsPerLine = 80;
        const wrappedLines = Math.ceil(content.length / avgCharsPerLine);
        const totalLines = Math.max(lines, wrappedLines);
        
        // Dynamic height calculation: 20px per line + padding
        const baseHeight = 200; // Minimum height
        const maxHeight = 600;  // Maximum height
        const calculatedHeight = Math.min(maxHeight, Math.max(baseHeight, totalLines * 20 + 80));
        
        return calculatedHeight;
      };
      
      setDynamicHeight(calculateHeight(data.input));
    }
  }, [data.input, data.lastUpdated]);

  // Handle scroll events
  useEffect(() => {
    const viewport = viewportRef.current;
    if (viewport) {
      const handleWheel = (e) => {
        // Only handle wheel events if content is scrollable
        if (viewport.scrollHeight > viewport.clientHeight) {
          e.stopPropagation();
          // Let the browser handle the scrolling naturally
        }
      };

      viewport.addEventListener('wheel', handleWheel, { passive: false });
      return () => {
        viewport.removeEventListener('wheel', handleWheel);
      };
    }
  }, [output]);

  return (
    <div className="node output-node">
      <Handle 
        type="target" 
        position={Position.Left} 
        id="input" 
        className="output-node-handle"
        style={{ 
          left: '-10px', 
          top: '50%', 
          transform: 'translateY(-50%)',
          position: 'absolute',
          zIndex: 10
        }}
      />
      
      <div className="node-header">
        <div className="header-left">
          <span className="node-icon">�</span>
          <div className="header-text">
            <span className="node-title">{data.label || 'Output Display'}</span>
            <span className="node-subtitle">AI Agent Response</span>
          </div>
        </div>
        <div className="node-status">
          <span className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`}>
            {isConnected ? '🟢' : '⚪'}
          </span>
          <span className="status-text">
            {isConnected 
              ? (output ? 'Live' : 'Connected') 
              : 'Disconnected'
            }
          </span>
        </div>
      </div>
      
      <div className="node-content">
        <div className="output-display">
          <div className="output-toolbar">
            <div className="toolbar-left">
              <span className="output-label">Response Output</span>
              {output && (
                <span className="content-indicator">
                  📄 {output.length} characters
                </span>
              )}
            </div>
            <div className="toolbar-right">
              {lastUpdated && (
                <span className="last-updated" title={`Last updated: ${new Date(lastUpdated).toLocaleString()}`}>
                  🕒 {new Date(lastUpdated).toLocaleTimeString()}
                </span>
              )}
              {output && (
                <button 
                  className="action-btn clear-btn"
                  onClick={() => {
                    setOutput('');
                    setLastUpdated('');
                  }}
                  title="Clear Output"
                >
                  🗑️
                </button>
              )}
              <button 
                className="action-btn copy-btn"
                onClick={() => {
                  if (output) {
                    navigator.clipboard.writeText(output);
                  }
                }}
                title="Copy to Clipboard"
                disabled={!output}
              >
                📋
              </button>
            </div>
          </div>
          <div 
            className="output-viewport" 
            ref={viewportRef}
            style={{ 
              height: `${dynamicHeight}px`,
              maxHeight: '600px',
              minHeight: '200px'
            }}
          >
            {output ? (
              <div className="output-content">
                <div className="content-text">{output}</div>
              </div>
            ) : (
              <div className="output-placeholder">
                <div className="placeholder-icon">🔌</div>
                <div className="placeholder-text">
                  {isConnected 
                    ? (connectedAgentId ? `Waiting for AI response...` : 'Connected, no data yet')
                    : 'Connect an AI agent to see output'
                  }
                </div>
                {!isConnected && (
                  <div className="placeholder-hint">
                    Connect the left handle to an AI agent's output
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      
      <div className="node-footer">
        <div className="footer-info">
          <span className="node-type">Output Terminal</span>
          <span className="separator">•</span>
          <span className="node-state">{data.status || 'Ready'}</span>
        </div>
      </div>
    </div>
  );
};

export default OutputNode;