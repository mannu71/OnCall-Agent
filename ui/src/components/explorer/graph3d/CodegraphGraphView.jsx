import { useEffect, useState, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Loader2 } from 'lucide-react';
import { agentApiClient } from '../../../services/agentApiClient';
import { GraphScene } from './GraphScene';
import { computeCameraTarget } from './sceneUtils';
import { Sidebar } from './Sidebar';
import { FilterPanel } from './FilterPanel';
import { NodeDetailPanel } from './NodeDetailPanel';
import { ResizeHandle } from './ResizeHandle';
import { ErrorBoundary } from './ErrorBoundary';

// Persist panel widths
function loadWidth(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v) return Math.max(150, Math.min(600, parseInt(v, 10)));
  } catch { /* ignore */ }
  return fallback;
}
function saveWidth(key, value) {
  try { localStorage.setItem(key, String(Math.round(value))); } catch { /* ignore */ }
}

// 3D force-directed graph view for the codegraph backend — replaces the
// crawler's 2D ReactFlow canvas since codegraph ships a real physics layout
// (layout3d.c) instead of a simple containment tree.
export default function CodegraphGraphView({ repo, sidebarContainer = null }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [highlightedIds, setHighlightedIds] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [cameraTarget, setCameraTarget] = useState(null);
  const [showLabels, setShowLabels] = useState(true);
  const [leftWidth, setLeftWidth] = useState(() => loadWidth('cbm-left-w', 260));
  const [rightWidth, setRightWidth] = useState(() => loadWidth('cbm-right-w', 280));

  // Filter state — all enabled by default
  const [enabledLabels, setEnabledLabels] = useState(new Set());
  const [enabledEdgeTypes, setEnabledEdgeTypes] = useState(new Set());

  const fetchOverview = useCallback(async (project) => {
    setLoading(true);
    setError(null);
    try {
      const result = await agentApiClient.getCodegraphLayout(project, { level: 'overview' });
      setData(result);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to fetch layout');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (repo) {
      fetchOverview(repo);
      setHighlightedIds(null);
      setSelectedPath(null);
      setSelectedNode(null);
    }
  }, [repo, fetchOverview]);

  // Initialize filters when data loads
  useEffect(() => {
    if (!data) return;
    const labels = new Set(data.nodes.map((n) => n.label));
    const types = new Set(data.edges.map((e) => e.type));
    setEnabledLabels(labels);
    setEnabledEdgeTypes(types);
  }, [data]);

  const filteredData = useMemo(() => {
    if (!data) return null;
    const nodes = data.nodes.filter((n) => enabledLabels.has(n.label));
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter(
      (e) => enabledEdgeTypes.has(e.type) && nodeIds.has(e.source) && nodeIds.has(e.target)
    );
    return { nodes, edges, total_nodes: data.total_nodes };
  }, [data, enabledLabels, enabledEdgeTypes]);

  const handleSelectPath = useCallback(
    (path, nodeIds) => {
      if (!filteredData || !path || nodeIds.size === 0) {
        setHighlightedIds(null);
        setSelectedPath(null);
        setCameraTarget(null);
        return;
      }
      setSelectedPath(path);
      setHighlightedIds(nodeIds);
      setCameraTarget(computeCameraTarget(filteredData.nodes, nodeIds));
    },
    [filteredData]
  );

  const handleNodeClick = useCallback(
    (node) => {
      if (!filteredData) return;
      setSelectedNode(node);

      const connectedIds = new Set([node.id]);
      for (const edge of filteredData.edges) {
        if (edge.source === node.id) connectedIds.add(edge.target);
        if (edge.target === node.id) connectedIds.add(edge.source);
      }
      setHighlightedIds(connectedIds);
      setSelectedPath(node.file_path ?? null);
      setCameraTarget(computeCameraTarget(filteredData.nodes, connectedIds));
    },
    [filteredData]
  );

  const toggleLabel = useCallback((label) => {
    setEnabledLabels((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  const toggleEdgeType = useCallback((type) => {
    setEnabledEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const enableAll = useCallback(() => {
    if (!data) return;
    setEnabledLabels(new Set(data.nodes.map((n) => n.label)));
    setEnabledEdgeTypes(new Set(data.edges.map((e) => e.type)));
  }, [data]);

  const disableAll = useCallback(() => {
    setEnabledLabels(new Set());
    setEnabledEdgeTypes(new Set());
  }, []);

  if (!repo) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted-foreground text-sm">Select a repo to view its graph</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-primary animate-spin mx-auto mb-3" />
          <p className="text-muted-foreground text-sm">Computing layout...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center p-8">
          <p className="text-destructive text-sm mb-2">{error}</p>
          <button
            onClick={() => fetchOverview(repo)}
            className="px-3 py-1.5 rounded-md border border-border bg-background text-xs hover:bg-muted transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data || !filteredData || filteredData.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-muted-foreground text-sm mb-3">
            {data && filteredData?.nodes.length === 0 ? 'All nodes filtered out' : 'No nodes in this project'}
          </p>
          {data && filteredData?.nodes.length === 0 && (
            <button
              onClick={enableAll}
              className="px-3 py-1.5 rounded-md border border-border bg-background text-xs hover:bg-muted transition-colors"
            >
              Reset Filters
            </button>
          )}
        </div>
      </div>
    );
  }

  // Filters + file tree. When the host provides `sidebarContainer` (the empty
  // outer Explorer panel for codegraph) this is portaled there so the 3D graph
  // gets the full width; otherwise it renders inline as a left sidebar.
  const sidebarContent = (
    <div className="flex flex-col h-full min-h-0 bg-card">
      {/* The filter chips are tall; cap + scroll them so the file-tree search
          below always stays in view (otherwise it drops off short viewports). */}
      <div className="shrink-0 overflow-y-auto custom-scrollbar max-h-[46%]">
        <FilterPanel
          data={data}
          enabledLabels={enabledLabels}
          enabledEdgeTypes={enabledEdgeTypes}
          showLabels={showLabels}
          onToggleLabel={toggleLabel}
          onToggleEdgeType={toggleEdgeType}
          onToggleShowLabels={() => setShowLabels((v) => !v)}
          onEnableAll={enableAll}
          onDisableAll={disableAll}
        />
      </div>
      <Sidebar nodes={filteredData.nodes} onSelectPath={handleSelectPath} selectedPath={selectedPath} />
    </div>
  );

  return (
    <div className="h-full flex">
      {sidebarContainer ? (
        createPortal(sidebarContent, sidebarContainer)
      ) : (
        <>
          <div
            className="border-r border-border flex flex-col h-full bg-card shrink-0"
            style={{ width: leftWidth }}
          >
            {sidebarContent}
          </div>
          <ResizeHandle
            side="left"
            onResize={(d) => {
              setLeftWidth((w) => {
                const nw = Math.max(150, Math.min(500, w + d));
                saveWidth('cbm-left-w', nw);
                return nw;
              });
            }}
          />
        </>
      )}

      {/* Graph area */}
      <div className="flex-1 relative overflow-hidden">
        <ErrorBoundary>
          <GraphScene
            data={filteredData}
            highlightedIds={highlightedIds}
            cameraTarget={cameraTarget}
            showLabels={showLabels}
            onNodeClick={handleNodeClick}
          />
        </ErrorBoundary>

        {/* HUD */}
        <div className="absolute top-4 left-4 text-[11px] text-white/30 pointer-events-none font-mono">
          <p>
            {filteredData.nodes.length.toLocaleString()} nodes / {filteredData.edges.length.toLocaleString()} edges
          </p>
          {data.nodes.length > filteredData.nodes.length && (
            <p className="text-white/25 mt-0.5">filtered from {data.nodes.length.toLocaleString()}</p>
          )}
          {highlightedIds && highlightedIds.size > 0 && (
            <p className="text-cyan-400/50 mt-0.5">{highlightedIds.size} selected</p>
          )}
        </div>

        <div className="absolute top-4 right-4 flex gap-2">
          {highlightedIds && (
            <button
              onClick={() => {
                setHighlightedIds(null);
                setSelectedPath(null);
                setSelectedNode(null);
                setCameraTarget(null);
              }}
              className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs hover:opacity-90 transition-opacity"
            >
              Clear
            </button>
          )}
          <button
            onClick={() => {
              setHighlightedIds(null);
              setSelectedPath(null);
              setSelectedNode(null);
              setCameraTarget(null);
              fetchOverview(repo);
            }}
            className="px-3 py-1.5 rounded-md border border-border bg-card/90 backdrop-blur text-xs hover:bg-muted transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Right detail panel — resizable */}
      {selectedNode && filteredData && (
        <>
          <ResizeHandle
            side="right"
            onResize={(d) => {
              setRightWidth((w) => {
                const nw = Math.max(200, Math.min(500, w + d));
                saveWidth('cbm-right-w', nw);
                return nw;
              });
            }}
          />
          <div className="dark border-l border-border shrink-0 h-full overflow-hidden" style={{ width: rightWidth, maxHeight: '100%' }}>
            <NodeDetailPanel
              node={selectedNode}
              allNodes={filteredData.nodes}
              allEdges={filteredData.edges}
              onClose={() => {
                setSelectedNode(null);
                setHighlightedIds(null);
                setSelectedPath(null);
              }}
              onNavigate={handleNodeClick}
            />
          </div>
        </>
      )}
    </div>
  );
}
