import React, { useCallback, useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  ReactFlowProvider
} from 'reactflow';

import 'reactflow/dist/style.css';
import '../../components/workflow/nodes/nodes.css';
import { nodeTypes } from '../../components/workflow/nodes/index.js';
import NodeSidebar from '../../components/workflow/NodeSidebar.jsx';
import NodeConfigPanel from '../../components/workflow/NodeConfigPanel.jsx';
import ErrorBoundary from '../../components/workflow/ErrorBoundary.jsx';
import { isValidConnection, getConnectionMessage } from '../../components/workflow/connectionValidation.js';

// Suppress ResizeObserver errors - these are harmless but annoying
const debounce = (func, wait) => {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
};

// More comprehensive ResizeObserver error handling
// Suppress ResizeObserver errors
if (typeof window !== 'undefined') {
  const originalError = console.error;
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('ResizeObserver')) return;
    originalError.apply(console, args);
  };
}

const Flow = forwardRef(({ initialNodes, initialEdges }, ref) => {
  const defaultInitialNodes = [
    {
      id: 'agent-initial',
      type: 'agent',
      position: { x: 400, y: 200 },
      data: {
        label: 'Agent AI',
        name: 'Agent AI',
        model: '',
        memory: '',
        tools: [],
        systemPrompt: 'You are a helpful AI assistant.',
        processing: false
      }
    }
  ];

  const reactFlowWrapper = useRef(null);
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes || defaultInitialNodes);
  const [edges, setEdges, defaultOnEdgesChange] = useEdgesState(initialEdges || []);
  const [reactFlowInstance, setReactFlowInstance] = useState(null);
  const [dragOverActive, setDragOverActive] = useState(false);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState('');
  const [messageType, setMessageType] = useState('info'); // 'info', 'success', 'error'

  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  // CRITICAL: Update nodes and edges when initialNodes/initialEdges change - no caching!
  // This ensures scheduler times and other workflow data stay in sync when reloaded from API
  useEffect(() => {
    if (initialNodes && initialNodes.length > 0) {
      setNodes(initialNodes);
    }
  }, [initialNodes, setNodes]);

  useEffect(() => {
    if (initialEdges) {
      setEdges(initialEdges);
    }
  }, [initialEdges, setEdges]);

  // Helper function to show message with type - wrapped in useCallback to avoid dependency issues
  const showMessage = useCallback((message, type = 'info', duration = 3000) => {
    setConnectionMessage(message);
    setMessageType(type);
    setTimeout(() => setConnectionMessage(''), duration);
  }, []);

  // Custom edge change handler to clean up agent data when LLM connections are removed
  const onEdgesChange = useCallback((changes) => {
    changes.forEach((change) => {
      if (change.type === 'remove') {
        const edge = edges.find(e => e.id === change.id);
        if (edge) {
          // Check if this was an LLM -> Agent connection
          const sourceNode = nodes.find(n => n.id === edge.source);
          const targetNode = nodes.find(n => n.id === edge.target);

          if (sourceNode?.type === 'llm' && targetNode?.type === 'agent' && edge.targetHandle === 'model') {
            console.log("🔌 Disconnecting LLM from agent:", targetNode.id);

            // Clean up agent data - reset status
            setNodes((nds) =>
              nds.map((node) =>
                node.id === targetNode.id
                  ? {
                    ...node,
                    data: {
                      ...node.data,
                      model: '',
                      initialized: false,
                      initializing: false,
                      processing: false
                    }
                  }
                  : node
              )
            );

            showMessage('LLM disconnected from agent', 'info', 2000);
          }
        }
      }
    });

    // Apply the default edge changes
    defaultOnEdgesChange(changes);
  }, [edges, nodes, setNodes, defaultOnEdgesChange, showMessage]);

  // Auto-zoom after ReactFlow is initialized
  useEffect(() => {
    if (reactFlowInstance) {
      // Give ReactFlow time to fully render the nodes (important!)
      requestAnimationFrame(() => {
        reactFlowInstance.fitView({ padding: 0.3, duration: 200 });

        // Optional: zoom-out slightly so nodes never look huge
        reactFlowInstance.zoomTo(0.6);
      });
    }
  }, [reactFlowInstance]);

  // Handle ResizeObserver errors gracefully
  useEffect(() => {
    const handleResize = debounce(() => {
      // Trigger a gentle re-render to help with layout issues
      if (reactFlowInstance) {
        reactFlowInstance.fitView({ duration: 0 });
      }
    }, 100);

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [reactFlowInstance]);

  // Shared logic to process a single agent with a message
  const processSingleAgent = useCallback(async (agentNode, message) => {
    const agentId = agentNode.id;
    const llmEdge = edgesRef.current.find(e => e.target === agentId && e.targetHandle === 'model');
    const llmNode = llmEdge ? nodesRef.current.find(n => n.id === llmEdge.source) : null;

    setNodes(nds => nds.map(n => {
      if (n.id === agentId) return { ...n, data: { ...n.data, processing: true, lastMessage: message } };
      if (n.id === llmNode?.id) return { ...n, data: { ...n.data, processing: true } };
      return n;
    }));

    try {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const response = `Processed by ${agentNode.data?.label || 'Agent'}: ${message}`;
      const outputEdges = edgesRef.current.filter(e => e.source === agentId && e.sourceHandle === 'agent-output');

      setNodes(nds => nds.map(n => {
        if (outputEdges.some(e => e.target === n.id)) {
          return { ...n, data: { ...n.data, input: response, lastUpdated: new Date().toISOString() } };
        }
        return n;
      }));
      return true;
    } finally {
      setNodes(nds => nds.map(n => {
        if (n.id === agentId) return { ...n, data: { ...n.data, processing: false } };
        if (n.id === llmNode?.id) return { ...n, data: { ...n.data, processing: false } };
        return n;
      }));
    }
  }, [setNodes]);

  const triggerTeamsNode = useCallback(async (nodeId, message = "Test message") => {
    const teamsNode = nodesRef.current.find(n => n.id === nodeId);
    if (!teamsNode) return;

    setNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, processing: true, status: 'Processing...' } } : n));

    try {
      await new Promise(resolve => setTimeout(resolve, 800));
      const outgoingEdges = edgesRef.current.filter(e => e.source === nodeId);
      let triggered = 0;

      for (const edge of outgoingEdges) {
        const target = nodesRef.current.find(n => n.id === edge.target);
        if (target?.type === 'agent') {
          if (await processSingleAgent(target, message)) triggered++;
        }
      }
      showMessage(`Teams triggered ${triggered} agent(s)`, triggered > 0 ? 'success' : 'info');
    } finally {
      setNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, processing: false, status: 'Connected' } } : n));
    }
  }, [setNodes, showMessage, processSingleAgent]);

  // Helper function to get edge color based on source node type
  const getEdgeColor = useCallback((sourceId) => {
    const sourceNode = nodes.find(n => n.id === sourceId);
    if (!sourceNode) return '#b1b1b7';

    const colorMap = {
      agent: '#ff6b6b',
      llm: '#4285f4',
      database: '#34a853',
      gmail: '#ea4335',
      teams: '#6264a7',
      tool: '#4caf50',
      memory: '#ff9800',
      chat: '#00bcd4'
    };

    return colorMap[sourceNode.type] || '#b1b1b7';
  }, [nodes]);

  // Expose getWorkflowData method to parent component
  useImperativeHandle(ref, () => ({
    getWorkflowData: () => ({ nodes: nodesRef.current, edges: edgesRef.current })
  }), []);

  // Keyboard shortcut handler for deleting nodes and edges
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Delete' || event.key === 'Backspace') {
        // Don't delete nodes when user is typing in an input field
        const activeElement = document.activeElement;
        const isTyping = activeElement && (
          activeElement.tagName === 'INPUT' ||
          activeElement.tagName === 'TEXTAREA' ||
          activeElement.contentEditable === 'true' ||
          activeElement.isContentEditable ||
          activeElement.closest('.config-panel') ||
          activeElement.closest('.chat-input-container-mini') ||
          activeElement.closest('input') ||
          activeElement.closest('textarea')
        );

        if (isTyping) {
          return; // Let the input field handle the backspace/delete
        }

        event.preventDefault();

        if (selectedEdge) {
          // Check if this was an LLM -> Agent connection and clean up
          const sourceNode = nodes.find(n => n.id === selectedEdge.source);
          const targetNode = nodes.find(n => n.id === selectedEdge.target);

          if (sourceNode?.type === 'llm' && targetNode?.type === 'agent' && selectedEdge.targetHandle === 'model') {
            console.log("🔌 Disconnecting LLM from agent via keyboard:", targetNode.id);

            // Clean up agent data - reset status
            setNodes((nds) =>
              nds.map((node) =>
                node.id === targetNode.id
                  ? {
                    ...node,
                    data: {
                      ...node.data,
                      model: '',
                      initialized: false,
                      initializing: false,
                      processing: false
                    }
                  }
                  : node
              )
            );

            showMessage('LLM disconnected from agent', 'info', 2000);
          }

          // Delete selected edge
          setEdges((eds) => eds.filter((edge) => edge.id !== selectedEdge.id));
          setSelectedEdge(null);
          showMessage('Connection deleted successfully', 'success', 2000);
        } else if (selectedNode) {
          // Delete selected node and its connections
          setNodes((nds) => nds.filter((node) => node.id !== selectedNode.id));
          setEdges((eds) => eds.filter((edge) =>
            edge.source !== selectedNode.id && edge.target !== selectedNode.id
          ));
          setSelectedNode(null);
          setShowConfigPanel(false);
          showMessage('Node and its connections deleted successfully', 'success', 2000);
        }
      }

      if (event.key === 'Escape') {
        setSelectedNode(null);
        setSelectedEdge(null);
        setShowConfigPanel(false);
      }

      if (event.key === 'p' || event.key === 'P') {
        // Don't trigger when user is typing in an input field
        const activeElement = document.activeElement;
        const isTyping = activeElement && (
          activeElement.tagName === 'INPUT' ||
          activeElement.tagName === 'TEXTAREA' ||
          activeElement.contentEditable === 'true' ||
          activeElement.isContentEditable ||
          activeElement.closest('.config-panel') ||
          activeElement.closest('.chat-input-container-mini') ||
          activeElement.closest('input') ||
          activeElement.closest('textarea')
        );

        if (isTyping) {
          return; // Let the input field handle the key press
        }

        event.preventDefault();

        // Find all Teams nodes
        const teamsNodes = nodes.filter(node => node.type === 'teams');

        if (teamsNodes.length === 0) {
          showMessage('No Teams nodes found in the flow', 'info', 2000);
          return;
        }

        if (teamsNodes.length === 1) {
          // If only one Teams node, trigger it directly
          const teamsNode = teamsNodes[0];
          console.log(`🎯 Triggering Teams node via 'P' key: ${teamsNode.id}`);
          triggerTeamsNode(teamsNode.id, "Analyse recent logs and suggest fix");
          showMessage(`Triggered Teams node: ${teamsNode.data?.label || 'Teams Channel'}`, 'success', 2000);
        } else {
          // If multiple Teams nodes, trigger all of them
          console.log(`🎯 Triggering ${teamsNodes.length} Teams nodes via 'P' key`);
          teamsNodes.forEach((teamsNode, index) => {
            // Stagger the triggers slightly to avoid overwhelming
            setTimeout(() => {
              triggerTeamsNode(teamsNode.id, `Analyse recent logs and suggest fix - Node ${index + 1}`);
            }, index * 200);
          });
          showMessage(`Triggered ${teamsNodes.length} Teams nodes`, 'success', 2000);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNode, selectedEdge, setNodes, setEdges, showMessage, nodes, triggerTeamsNode]); const onConnect = useCallback(
    async (params) => {
      // Validate connection before adding
      if (isValidConnection(params.source, params.target, params.sourceHandle, params.targetHandle, nodes, edges)) {
        setEdges((eds) => addEdge({
          ...params,
          animated: true,
          style: {
            strokeWidth: 2,
            stroke: getEdgeColor(params.source)
          },
          type: 'default'
        }, eds));

        // Check if this is an LLM connecting to an Agent's model handle
        const sourceNode = nodes.find(n => n.id === params.source);
        const targetNode = nodes.find(n => n.id === params.target);

        if (sourceNode?.type === 'llm' && targetNode?.type === 'agent' && params.targetHandle === 'model') {
          // Initialize the agent with the connected LLM model
          try {
            const selectedModel = sourceNode.data?.model || sourceNode.data?.label || 'llama3-8b-8192';
            const llmApiKeyEnvVar = sourceNode.data?.apiKeyEnvVar;

            // Check if LLM node has API key env var configured
            if (!llmApiKeyEnvVar) {
              showMessage('Warning: LLM node has no API key environment variable configured. Please set it in the LLM node settings.', 'warning', 5000);
            }

            // Set agent to initializing state
            setNodes((nds) =>
              nds.map((node) =>
                node.id === params.target
                  ? {
                    ...node,
                    data: {
                      ...node.data,
                      initializing: true,
                      model: selectedModel,
                      status: `Initializing agent with ${selectedModel}...`
                    }
                  }
                  : node
              )
            );

            // Set agent as initialized (no need for client-side LLM initialization)
            setNodes((nds) =>
              nds.map((node) =>
                node.id === params.target
                  ? {
                    ...node,
                    data: {
                      ...node.data,
                      initializing: false,
                      initialized: true,
                      status: 'Agent ready'
                    }
                  }
                  : node
              )
            );

            showMessage(`Agent initialized with ${selectedModel} model`, 'success', 3000);
          } catch (error) {
            console.error('Failed to initialize agent:', error);

            // Update agent state to show error
            setNodes((nds) =>
              nds.map((node) =>
                node.id === params.target
                  ? {
                    ...node,
                    data: {
                      ...node.data,
                      initializing: false,
                      initialized: false,
                      status: 'Initialization failed'
                    }
                  }
                  : node
              )
            );

            showMessage('Failed to initialize agent. Check console for details.', 'error', 5000);
          }
        } else {
          showMessage('Connection created successfully', 'success', 2000);
        }
      } else {
        const message = getConnectionMessage(params.source, params.target, params.sourceHandle, params.targetHandle, nodes, edges);
        showMessage(message, 'error', 3000);
      }
    },
    [setEdges, nodes, edges, getEdgeColor, showMessage, setNodes],
  );

  const onConnectStart = useCallback(() => {
    setConnectionMessage('');
  }, []);

  const onNodeClick = useCallback((event, node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
    // Don't show config panel for chat nodes, output nodes, tool nodes, and llm nodes
    if (node.type !== 'chat' && node.type !== 'output' && node.type !== 'tool' && node.type !== 'llm') {
      setShowConfigPanel(true);
    } else {
      setShowConfigPanel(false);
    }
  }, []);

  const onEdgeClick = useCallback((event, edge) => {
    event.stopPropagation();
    setSelectedEdge(edge);
    setSelectedNode(null);
    setShowConfigPanel(false);

    // Update edge styling to show selection
    setEdges((eds) => eds.map((e) => ({
      ...e,
      className: e.id === edge.id ? 'selected' : ''
    })));

    showMessage('Connection selected - Press Delete to remove', 'info', 3000);
  }, [setEdges, showMessage]);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
    setShowConfigPanel(false);

    // Clear edge selection styling
    setEdges((eds) => eds.map((e) => ({
      ...e,
      className: ''
    })));
  }, [setEdges]);

  const onConfigUpdate = useCallback((nodeId, newData) => {
    console.log('[WorkflowEditor] onConfigUpdate called for node:', nodeId, 'with data:', newData);
    setNodes((nds) => {
      const updatedNodes = nds.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, ...newData } }
          : node
      );
      console.log('[WorkflowEditor] Updated nodes:', updatedNodes.find(n => n.id === nodeId)?.data);
      return updatedNodes;
    });
  }, [setNodes]);

  const sendMessageToConnectedAgents = useCallback(async (sourceId, message) => {
    const outgoing = edgesRef.current.filter(e => e.source === sourceId && e.sourceHandle === 'chat-output');
    if (!outgoing.length) {
      showMessage('No connected agents found', 'info');
      return;
    }

    let triggered = 0;
    for (const edge of outgoing) {
      const target = nodesRef.current.find(n => n.id === edge.target);
      if (target?.type === 'agent') {
        if (await processSingleAgent(target, message)) triggered++;
      }
    }
    showMessage(`Message sent to ${outgoing.length} agent(s), ${triggered} processed`, triggered > 0 ? 'success' : 'warning');
  }, [showMessage, processSingleAgent]);

  // Expose trigger function globally for dev console access
  useEffect(() => {
    // Function to list all Teams nodes
    const listTeamsNodes = () => {
      const teamsNodes = nodes.filter(node => node.type === 'teams');
      console.log("📋 Available Teams nodes:", teamsNodes.map(node => ({
        id: node.id,
        label: node.data?.label || 'Teams Channel',
        channel: node.data?.channel || 'General Channel',
        connected: edges.some(edge => edge.source === node.id || edge.target === node.id)
      })));
      return teamsNodes;
    };

    // Expose functions to global window object
    window.triggerTeamsNode = triggerTeamsNode;
    window.listTeamsNodes = listTeamsNodes;

    // Cleanup on unmount
    return () => {
      delete window.triggerTeamsNode;
      delete window.listTeamsNodes;
    };
  }, [triggerTeamsNode, nodes, edges]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setDragOverActive(true);
  }, []);

  const onDragLeave = useCallback(() => {
    setDragOverActive(false);
  }, []);

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();
      setDragOverActive(false);

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const nodeData = JSON.parse(event.dataTransfer.getData('application/reactflow'));

      // Check if the dropped element is valid
      if (typeof nodeData === 'undefined' || !nodeData) {
        return;
      }

      const position = reactFlowInstance.project({
        x: event.clientX - reactFlowBounds.left,
        y: event.clientY - reactFlowBounds.top,
      });

      const newNode = {
        id: `${nodeData.type}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        type: nodeData.type,
        position,
        data: nodeData.data,
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [reactFlowInstance, setNodes],
  );

  return (
    <div class="flex h-[calc(100vh-90px)] w-full relative">
      <NodeSidebar />
      <div class="flex-1 h-full" ref={reactFlowWrapper}>
        {connectionMessage && (
          <div className={`connection-message ${messageType}`}>
            {messageType === 'success' ? '✅' : messageType === 'error' ? '❌' : 'ℹ️'} {connectionMessage}
          </div>
        )}
        <ErrorBoundary>
          <ReactFlowProvider>
            <ReactFlow
              nodes={nodes.map(node => ({
                ...node,
                data: {
                  ...node.data,
                  sendMessageToConnectedAgents: node.type === 'chat' ? sendMessageToConnectedAgents : undefined,
                  hasConnections: node.type === 'chat' ? edges.some(edge =>
                    edge.source === node.id && edge.sourceHandle === 'chat-output'
                  ) : undefined
                }
              }))}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onConnectStart={onConnectStart}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
              onInit={setReactFlowInstance}
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              nodeTypes={nodeTypes}
              isValidConnection={(connection) =>
                isValidConnection(connection.source, connection.target, connection.sourceHandle, connection.targetHandle, nodes, edges)
              }
              className={dragOverActive ? 'drop-active' : ''}
              defaultEdgeOptions={{
                animated: true,
                style: { strokeWidth: 2 },
                type: 'default',
              }}
              connectionLineStyle={{
                strokeWidth: 2,
                stroke: '#b1b1b7',
                strokeDasharray: '5,5'
              }}
              deleteKeyCode={['Delete', 'Backspace']}
              selectNodesOnDrag={false}
              panOnScroll={true}
              panOnScrollSpeed={0.5}
              zoomOnScroll={true}
              zoomOnPinch={true}
              preventScrolling={false}
              attributionPosition="top-right"
            >
              <Controls />
              <MiniMap
                nodeStrokeColor={(n) => {
                  if (n.type === 'agent') return '#ff6b6b';
                  if (n.type === 'llm') return '#4285f4';
                  if (n.type === 'database') return '#34a853';
                  if (n.type === 'gmail') return '#ea4335';
                  if (n.type === 'teams') return '#6264a7';
                  if (n.type === 'chat') return '#00bcd4';
                  return '#ddd';
                }}
                nodeColor={(n) => {
                  if (n.type === 'agent') return '#fff5f5';
                  if (n.type === 'llm') return '#f0f7ff';
                  if (n.type === 'database') return '#f0fff4';
                  if (n.type === 'gmail') return '#fff5f5';
                  if (n.type === 'teams') return '#f8f7ff';
                  if (n.type === 'chat') return '#e0f2f1';
                  return '#fff';
                }}
              />
              <Background variant="dots" gap={20} size={1} />
            </ReactFlow>
          </ReactFlowProvider>
        </ErrorBoundary>
      </div>

      {showConfigPanel && (
        <NodeConfigPanel
          node={selectedNode}
          onUpdate={onConfigUpdate}
          onClose={() => setShowConfigPanel(false)}
        />
      )}
    </div>
  );
});

Flow.displayName = 'WorkflowEditor';

export default Flow;