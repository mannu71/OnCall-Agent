import React, { useState, useEffect, useCallback, useMemo } from 'react';
import ReactFlow, {
  MiniMap,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Folder,
  FileCode,
  Network,
  Search,
  Activity,
  GitBranch,
  ShieldCheck,
  Play,
  ArrowRight,
  Info,
  Layers,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Trash2,
  Loader2
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { agentApiClient } from '../services/agentApiClient';
import DiffImpactPanel from '../components/explorer/DiffImpactPanel';

// Curated colors for node types to wow the user (Premium details badge styles)
const KIND_COLORS = {
  class: 'bg-blue-500/10 border border-blue-500/30 text-blue-700 dark:text-blue-300',
  interface: 'bg-indigo-500/10 border border-indigo-500/30 text-indigo-700 dark:text-indigo-300',
  method: 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300',
  function: 'bg-teal-500/10 border border-teal-500/30 text-teal-700 dark:text-teal-300',
  route: 'bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300',
  enum: 'bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-300',
  test: 'bg-purple-500/10 border border-purple-500/30 text-purple-700 dark:text-purple-300',
};

const DEFAULT_COLOR = 'bg-slate-500/10 border border-slate-500/30 text-slate-700 dark:text-slate-300';

const NODE_CLASSES = {
  class: 'bg-blue-500/10 dark:bg-blue-500/20 border border-blue-500/40 text-blue-700 dark:text-blue-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-blue-500/25 transition-all font-medium',
  interface: 'bg-indigo-500/10 dark:bg-indigo-500/20 border border-indigo-500/40 text-indigo-700 dark:text-indigo-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-indigo-500/25 transition-all font-medium',
  method: 'bg-emerald-500/10 dark:bg-emerald-500/20 border border-emerald-500/40 text-emerald-700 dark:text-emerald-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-emerald-500/25 transition-all font-medium',
  function: 'bg-teal-500/10 dark:bg-teal-500/20 border border-teal-500/40 text-teal-700 dark:text-teal-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-teal-500/25 transition-all font-medium',
  route: 'bg-rose-500/10 dark:bg-rose-500/20 border border-rose-500/40 text-rose-800 dark:text-rose-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-rose-500/25 transition-all font-medium',
  enum: 'bg-amber-500/10 dark:bg-amber-500/20 border border-amber-500/40 text-amber-700 dark:text-amber-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-amber-500/25 transition-all font-medium',
  test: 'bg-purple-500/10 dark:bg-purple-500/20 border border-purple-500/40 text-purple-700 dark:text-purple-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-purple-500/25 transition-all font-medium',
};

const DEFAULT_NODE_CLASS = 'bg-slate-500/10 dark:bg-slate-500/20 border border-slate-500/40 text-slate-800 dark:text-slate-200 rounded-md p-2.5 text-xs w-[220px] text-center shadow-sm cursor-pointer hover:bg-slate-500/25 transition-all font-medium';

// A response is "missing" intelligence when the backend returns nothing or an
// explicit { error } shape (e.g. a 404 surfaced as data). Treat both as
// "not generated yet" so the panel renders a hint instead of crashing.
function _hasIntel(res) {
  return !!res && typeof res === 'object' && !res.error;
}

// Read-only, collapsible project-intelligence viewer for an indexed repo.
// Lazily fetches brief / standards / modules when first expanded and caches
// them in local state. Never mutates the index.
function ProjectIntelligencePanel({ repo }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [brief, setBrief] = useState(null);
  const [standards, setStandards] = useState(null);
  const [modules, setModules] = useState([]);
  const [expandedModule, setExpandedModule] = useState(null); // path of expanded module
  const [moduleDetail, setModuleDetail] = useState({});        // path -> detail
  const [moduleLoading, setModuleLoading] = useState('');      // path currently loading

  // Reset cached intelligence whenever the selected repo changes.
  useEffect(() => {
    setOpen(false);
    setBrief(null);
    setStandards(null);
    setModules([]);
    setExpandedModule(null);
    setModuleDetail({});
    setError('');
  }, [repo]);

  const load = useCallback(async () => {
    if (!repo) return;
    setLoading(true);
    setError('');
    try {
      const [briefRes, standardsRes, docsRes] = await Promise.all([
        agentApiClient.getRepoBrief(repo).catch(() => null),
        agentApiClient.getRepoStandards(repo).catch(() => null),
        agentApiClient.getRepoDocs(repo).catch(() => null),
      ]);
      setBrief(_hasIntel(briefRes) ? briefRes : null);
      setStandards(_hasIntel(standardsRes) ? standardsRes : null);
      const mods = _hasIntel(docsRes) ? (docsRes.modules || docsRes.docs || []) : [];
      setModules(Array.isArray(mods) ? mods : []);
    } catch (err) {
      console.error('Failed to load project intelligence', err);
      setError(err?.message || 'Failed to load project intelligence');
    } finally {
      setLoading(false);
    }
  }, [repo]);

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    // Lazy-load on first expand.
    if (next && brief === null && standards === null && modules.length === 0 && !loading) {
      load();
    }
  };

  const handleModuleClick = useCallback(async (path) => {
    if (expandedModule === path) {
      setExpandedModule(null);
      return;
    }
    setExpandedModule(path);
    if (moduleDetail[path] !== undefined) return; // cached
    setModuleLoading(path);
    try {
      const res = await agentApiClient.getRepoDocs(repo, path);
      setModuleDetail(prev => ({ ...prev, [path]: _hasIntel(res) ? res : null }));
    } catch (err) {
      console.error('Failed to load module detail', err);
      setModuleDetail(prev => ({ ...prev, [path]: null }));
    } finally {
      setModuleLoading('');
    }
  }, [repo, expandedModule, moduleDetail]);

  const nothing = !loading && !error && !brief && !standards && modules.length === 0;
  const domainModel = brief?.domain_model || [];

  return (
    <div className="border-t border-border">
      <button
        onClick={handleToggle}
        className="w-full flex items-center gap-2 px-5 py-3 text-left text-xs font-semibold text-foreground hover:bg-muted transition-colors"
      >
        <ChevronRight className={`w-3.5 h-3.5 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`} />
        <Layers className="w-4 h-4 text-primary" />
        <span>Project Intelligence</span>
        {loading && <Activity className="w-3.5 h-3.5 text-primary animate-spin ml-auto" />}
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-5 text-xs">
          {error && (
            <p className="text-[11px] text-destructive leading-snug">{error}</p>
          )}

          {nothing && (
            <p className="text-muted-foreground italic leading-relaxed">
              No project intelligence yet — reindex to generate.
            </p>
          )}

          {/* Project brief + architecture */}
          {brief && (
            <div className="space-y-3">
              {brief.brief && (
                <div className="space-y-1.5">
                  <h4 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Project Brief</h4>
                  <p className="text-foreground leading-relaxed bg-muted/40 p-3 rounded-md border border-border whitespace-pre-wrap">
                    {brief.brief}
                  </p>
                </div>
              )}
              {brief.architecture && (
                <div className="space-y-1.5">
                  <h4 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Architecture</h4>
                  <p className="text-foreground leading-relaxed bg-muted/40 p-3 rounded-md border border-border whitespace-pre-wrap">
                    {brief.architecture}
                  </p>
                </div>
              )}
              {domainModel.length > 0 && (
                <div className="space-y-1.5">
                  <h4 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Domain Model</h4>
                  <ul className="space-y-1.5">
                    {domainModel.map((d, i) => (
                      <li key={d.entity || d.name || i} className="bg-muted/40 p-2.5 rounded-md border border-border">
                        <span className="font-semibold text-foreground">{d.entity || d.name}</span>
                        {(d.description || d.desc) && (
                          <span className="text-muted-foreground"> — {d.description || d.desc}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Coding standards */}
          {standards && (
            <div className="space-y-3">
              <h4 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Coding Standards</h4>
              {Array.isArray(standards.naming) && standards.naming.length > 0 && (
                <div className="space-y-1">
                  <span className="text-[10px] font-semibold text-foreground">Naming</span>
                  <ul className="list-disc pl-4 space-y-0.5 text-muted-foreground">
                    {standards.naming.map((n, i) => <li key={i} className="break-words">{n}</li>)}
                  </ul>
                </div>
              )}
              {standards.layout && (
                <div className="space-y-1">
                  <span className="text-[10px] font-semibold text-foreground">Layout</span>
                  <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">{standards.layout}</p>
                </div>
              )}
              {Array.isArray(standards.frameworks) && standards.frameworks.length > 0 && (
                <div className="space-y-1">
                  <span className="text-[10px] font-semibold text-foreground">Frameworks</span>
                  <div className="flex flex-wrap gap-1.5">
                    {standards.frameworks.map((f, i) => (
                      <span key={i} className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded border border-border">{f}</span>
                    ))}
                  </div>
                </div>
              )}
              {standards.error_handling && (
                <div className="space-y-1">
                  <span className="text-[10px] font-semibold text-foreground">Error Handling</span>
                  <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">{standards.error_handling}</p>
                </div>
              )}
            </div>
          )}

          {/* Modules */}
          {modules.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Modules</h4>
              <div className="space-y-1">
                {modules.map((m, i) => {
                  const path = m.path || m.module || m.name;
                  const isExpanded = expandedModule === path;
                  const detail = moduleDetail[path];
                  return (
                    <div key={path || i} className="rounded-md border border-border overflow-hidden">
                      <button
                        onClick={() => handleModuleClick(path)}
                        className="w-full text-left px-2.5 py-2 flex items-start gap-2 hover:bg-muted transition-colors"
                      >
                        <ChevronRight className={`w-3 h-3 mt-0.5 text-muted-foreground flex-shrink-0 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
                        <span className="flex-1 min-w-0">
                          <span className="font-semibold text-foreground break-words block">{path}</span>
                          {m.responsibility && (
                            <span className="text-muted-foreground text-[11px] leading-snug">{m.responsibility}</span>
                          )}
                        </span>
                        {moduleLoading === path && <Activity className="w-3 h-3 text-primary animate-spin flex-shrink-0" />}
                      </button>
                      {isExpanded && (
                        <div className="px-3 pb-3 pt-1 space-y-2 bg-muted/30 border-t border-border">
                          {detail === null && (
                            <p className="text-muted-foreground italic text-[11px]">No further detail available.</p>
                          )}
                          {detail && Array.isArray(detail.key_components) && detail.key_components.length > 0 && (
                            <div className="space-y-1">
                              <span className="text-[10px] font-semibold text-foreground">Key Components</span>
                              <ul className="list-disc pl-4 space-y-0.5 text-muted-foreground">
                                {detail.key_components.map((c, j) => (
                                  <li key={j} className="break-words">{typeof c === 'string' ? c : (c.name || c.component)}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {detail && detail.data_flow && (
                            <div className="space-y-1">
                              <span className="text-[10px] font-semibold text-foreground">Data Flow</span>
                              <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">
                                {typeof detail.data_flow === 'string' ? detail.data_flow : JSON.stringify(detail.data_flow)}
                              </p>
                            </div>
                          )}
                          {detail && Array.isArray(detail.depends_on) && detail.depends_on.length > 0 && (
                            <div className="space-y-1">
                              <span className="text-[10px] font-semibold text-foreground">Depends On</span>
                              <div className="flex flex-wrap gap-1.5">
                                {detail.depends_on.map((d, j) => (
                                  <span key={j} className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded border border-border break-words">
                                    {typeof d === 'string' ? d : (d.path || d.name)}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CodebaseExplorer() {
  const [repos, setRepos] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState('');
  const [files, setFiles] = useState([]);
  const [filteredFiles, setFilteredFiles] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [activeTab, setActiveTab] = useState('structure'); // 'structure' | 'domain' | 'impact'
  const [selectedNode, setSelectedNode] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false); // reindex / delete in flight
  const [actionError, setActionError] = useState('');

  // Graph state
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Diff impact state
  const [diffFilesText, setDiffFilesText] = useState('');
  const [diffSymbolsText, setDiffSymbolsText] = useState('');
  const [diffImpactResult, setDiffImpactResult] = useState(null);

  // 1. Fetch available indexed repositories. Returns the repo list so callers
  //    (initial load, post-reindex, post-delete) can react to the new state.
  const loadRepos = useCallback(async ({ preferRepo } = {}) => {
    try {
      const res = await agentApiClient.listCrawlerRepos();
      const list = res?.repos || [];
      setRepos(list);
      if (list.length > 0) {
        const stillThere = preferRepo && list.some(r => r.repo_name === preferRepo);
        setSelectedRepo(stillThere ? preferRepo : list[0].repo_name);
      } else {
        setSelectedRepo('');
      }
      return list;
    } catch (err) {
      console.error('Failed to load repositories', err);
      return [];
    }
  }, []);

  useEffect(() => {
    loadRepos();
  }, [loadRepos]);

  // Force a full rebuild of the selected repo's index, then refresh counts.
  const handleReindex = useCallback(async () => {
    if (!selectedRepo || isMutating) return;
    setIsMutating(true);
    setActionError('');
    try {
      await agentApiClient.reindexRepo(selectedRepo);
      await loadRepos({ preferRepo: selectedRepo });
      // Refresh the file list for the rebuilt index.
      const res = await agentApiClient.getRepoFiles(selectedRepo, null, false, 500);
      if (res?.files) {
        setFiles(res.files);
        setFilteredFiles(res.files);
      }
    } catch (err) {
      console.error('Reindex failed', err);
      setActionError(`Reindex failed: ${err?.message || 'unknown error'}`);
    } finally {
      setIsMutating(false);
    }
  }, [selectedRepo, isMutating, loadRepos]);

  // Delete the entire index for the selected repo after explicit confirmation.
  const handleDeleteIndex = useCallback(async () => {
    if (!selectedRepo || isMutating) return;
    const confirmed = window.confirm(
      `Delete the entire index for "${selectedRepo}"?\n\n` +
      'This removes its knowledge graph and overview. You can rebuild it later by re-indexing.'
    );
    if (!confirmed) return;
    setIsMutating(true);
    setActionError('');
    try {
      await agentApiClient.deleteIndex(selectedRepo);
      const remaining = await loadRepos();
      if (remaining.length === 0) {
        setFiles([]);
        setFilteredFiles([]);
        setSelectedFile(null);
        setSelectedNode(null);
        setNodes([]);
        setEdges([]);
        setDiffImpactResult(null);
      }
    } catch (err) {
      console.error('Delete index failed', err);
      setActionError(`Delete failed: ${err?.message || 'unknown error'}`);
    } finally {
      setIsMutating(false);
    }
  }, [selectedRepo, isMutating, loadRepos, setNodes, setEdges]);

  // 2. Fetch files for selected repository
  useEffect(() => {
    if (!selectedRepo) return;
    async function loadFiles() {
      setIsLoading(true);
      try {
        const res = await agentApiClient.getRepoFiles(selectedRepo, null, false, 500);
        if (res?.files) {
          setFiles(res.files);
          setFilteredFiles(res.files);
        }
      } catch (err) {
        console.error('Failed to load repo files', err);
      } finally {
        setIsLoading(false);
      }
    }
    loadFiles();
    setSelectedFile(null);
    setSelectedNode(null);
    setNodes([]);
    setEdges([]);
    setDiffImpactResult(null);
  }, [selectedRepo]);

  // 3. Filter files by search query
  useEffect(() => {
    if (!searchQuery) {
      setFilteredFiles(files);
      return;
    }
    const q = searchQuery.toLowerCase();
    setFilteredFiles(
      files.filter(f => f.file.toLowerCase().includes(q) || (f.language || '').toLowerCase().includes(q))
    );
  }, [searchQuery, files]);

  // Group files by deterministic domain mapping
  const domainGroups = useMemo(() => {
    const groups = {};
    files.forEach(f => {
      // Normalize slashes
      const path = f.file.replace(/\\/g, '/');
      const parts = path.split('/');
      
      let domain = 'Shared';
      // Basic deterministic path mapping
      const srcIdx = parts.findIndex(p => ['src', 'app', 'endpoints', 'services', 'components'].includes(p.toLowerCase()));
      if (srcIdx !== -1 && srcIdx + 1 < parts.length - 1) {
        const cand = parts[srcIdx + 1];
        if (!['controllers', 'services', 'repositories', 'models', 'endpoints'].includes(cand.toLowerCase())) {
          domain = cand.charAt(0).toUpperCase() + cand.slice(1);
        }
      } else if (parts.length > 1) {
        domain = parts[parts.length - 2].charAt(0).toUpperCase() + parts[parts.length - 2].slice(1);
      }
      
      if (!groups[domain]) groups[domain] = [];
      groups[domain].push(f);
    });
    return groups;
  }, [files]);

  // 4. Render a file's code entities visually using React Flow (Zero LLM cost)
  const handleFileClick = useCallback(async (fileObj) => {
    setSelectedFile(fileObj);
    setIsLoading(true);
    try {
      // Fetch all nodes and edges inside this file using our new API
      const res = await agentApiClient.getFileNodes(selectedRepo, fileObj.file);
      
      const newNodes = [];
      const newEdges = [];

      // 1. Add the file node
      const fileNodeId = `file:${fileObj.file}`;
      newNodes.push({
        id: fileNodeId,
        type: 'default',
        data: { label: `📄 ${fileObj.file.split('/').pop()}` },
        position: { x: 350, y: 20 },
        className: 'bg-card border-2 border-primary/50 text-foreground rounded-lg p-3 font-semibold w-[250px] text-center shadow-md transition-colors',
      });

      // 2. Add child nodes
      const dbNodes = res?.nodes || [];
      const dbEdges = res?.edges || [];

      // Calculate layout coordinates
      const columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(dbNodes.length || 1))));
      
      dbNodes.forEach((node, i) => {
        const col = i % columns;
        const row = Math.floor(i / columns);
        
        // Premium styles based on node kind using Tailwind className
        const kindClass = NODE_CLASSES[node.kind] || DEFAULT_NODE_CLASS;

        newNodes.push({
          id: node.qualified_name,
          data: { label: `${node.name}\n(${node.kind})` },
          position: { x: 50 + col * 280, y: 180 + row * 150 },
          className: kindClass,
        });

        // Add parent-child containment edge or file containment edge
        if (node.parent_name) {
          // Find if parent exists in the file nodes
          const hasParentInFile = dbNodes.some(n => n.name === node.parent_name || n.qualified_name === node.parent_name);
          const parentId = hasParentInFile 
            ? dbNodes.find(n => n.name === node.parent_name || n.qualified_name === node.parent_name).qualified_name
            : fileNodeId;
            
          newEdges.push({
            id: `contains-${parentId}-${node.qualified_name}`,
            source: parentId,
            target: node.qualified_name,
            style: { stroke: 'rgba(148, 163, 184, 0.25)', strokeDasharray: '3,3', strokeWidth: 1 },
          });
        } else {
          // Connect directly to the file node
          newEdges.push({
            id: `contains-${fileNodeId}-${node.qualified_name}`,
            source: fileNodeId,
            target: node.qualified_name,
            style: { stroke: 'rgba(148, 163, 184, 0.3)', strokeDasharray: '3,3', strokeWidth: 1.2 },
          });
        }
      });

      // 3. Add intra-file call/ref edges
      dbEdges.forEach(edge => {
        const sourceExists = dbNodes.some(n => n.qualified_name === edge.source_qname);
        const targetExists = dbNodes.some(n => n.qualified_name === edge.target_qname);

        if (sourceExists && targetExists) {
          newEdges.push({
            id: `call-${edge.source_qname}-${edge.target_qname}`,
            source: edge.source_qname,
            target: edge.target_qname,
            label: edge.kind === 'calls' ? '' : edge.kind,
            labelStyle: { fill: '#94a3b8', fontSize: '9px', fontWeight: '500' },
            markerEnd: { type: MarkerType.ArrowClosed, color: 'rgba(16, 185, 129, 0.6)' },
            style: { stroke: 'rgba(16, 185, 129, 0.45)', strokeWidth: 1.5 },
          });
        }
      });

      setNodes(newNodes);
      setEdges(newEdges);

      // Set the selected node to the file itself initially
      setSelectedNode({
        name: fileObj.file.split('/').pop(),
        qualified_name: fileObj.file,
        kind: 'file',
        file: fileObj.file,
        language: fileObj.language,
        size_bytes: fileObj.size_bytes,
        node_count: fileObj.node_count,
        edge_count: fileObj.edge_count,
        parse_error: fileObj.parse_error,
      });

    } catch (err) {
      console.error('Failed to visualize file', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedRepo, setNodes, setEdges]);

  // 5. Click on visual node to fetch node metadata (Deterministic call details)
  const onNodeClick = useCallback(async (event, flowNode) => {
    if (flowNode.id.startsWith('file:')) return;
    setIsLoading(true);
    try {
      const res = await agentApiClient.getKGNode(selectedRepo, flowNode.id);
      if (res && !res.error) {
        setSelectedNode(res);
      }
    } catch (err) {
      console.error('Failed to fetch node metadata', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedRepo]);

  // 6. Trace path forward/backward dynamically when double clicked (Interactive call trees)
  const onNodeDoubleCliick = useCallback(async (event, flowNode) => {
    if (flowNode.id.startsWith('file:')) return;
    setIsLoading(true);
    try {
      const callersRes = await agentApiClient.getKGCallers(selectedRepo, flowNode.id, 2);
      const calleesRes = await agentApiClient.getKGCallees(selectedRepo, flowNode.id, 2);

      const addedNodes = [];
      const addedEdges = [];

      // Add callers (upstream dependencies)
      if (callersRes?.edges) {
        callersRes.edges.forEach((edge, i) => {
          addedNodes.push({
            id: edge.from,
            data: { label: `${edge.from.split('::').pop()}\n(Caller)` },
            position: { x: flowNode.position.x - 250, y: flowNode.position.y + (i - 1) * 120 },
            className: 'bg-red-500/10 dark:bg-red-500/20 border border-red-500/40 text-red-700 dark:text-red-300 rounded-md p-2.5 text-xs w-[180px] text-center shadow-sm font-medium transition-all',
          });
          addedEdges.push({
            id: `edge-${edge.from}-${flowNode.id}`,
            source: edge.from,
            target: flowNode.id,
            markerEnd: { type: MarkerType.ArrowClosed, color: '#f87171' },
            style: { stroke: '#f87171', strokeWidth: 1.5 },
          });
        });
      }

      // Add callees (downstream dependencies)
      if (calleesRes?.edges) {
        calleesRes.edges.forEach((edge, i) => {
          addedNodes.push({
            id: edge.to,
            data: { label: `${edge.to.split('::').pop()}\n(Callee)` },
            position: { x: flowNode.position.x + 250, y: flowNode.position.y + (i - 1) * 120 },
            className: 'bg-emerald-500/10 dark:bg-emerald-500/20 border border-emerald-500/40 text-emerald-700 dark:text-emerald-300 rounded-md p-2.5 text-xs w-[180px] text-center shadow-sm font-medium transition-all',
          });
          addedEdges.push({
            id: `edge-${flowNode.id}-${edge.to}`,
            source: flowNode.id,
            target: edge.to,
            markerEnd: { type: MarkerType.ArrowClosed, color: '#34d399' },
            style: { stroke: '#34d399', strokeWidth: 1.5 },
          });
        });
      }

      // Merge on canvas
      setNodes(prev => {
        const unique = [...prev];
        addedNodes.forEach(node => {
          if (!unique.some(u => u.id === node.id)) unique.push(node);
        });
        return unique;
      });

      setEdges(prev => {
        const unique = [...prev];
        addedEdges.forEach(edge => {
          if (!unique.some(u => u.id === edge.id)) unique.push(edge);
        });
        return unique;
      });

    } catch (err) {
      console.error('Failed to expand dependencies', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedRepo, setNodes, setEdges]);

  // 7. Compute deterministic Diff Impact Analysis
  const handleRunDiffImpact = async () => {
    setIsLoading(true);
    setDiffImpactResult(null);
    try {
      const filesArr = diffFilesText.split('\n').map(x => x.trim()).filter(Boolean);
      const symbolsArr = diffSymbolsText.split('\n').map(x => x.trim()).filter(Boolean);

      const res = await agentApiClient.getKGDiffImpact(selectedRepo, filesArr, symbolsArr);
      setDiffImpactResult(res);

      // Render the impact visually on the canvas
      if (res?.impacted_nodes && res.impacted_nodes.length > 0) {
        const canvasNodes = [];
        const canvasEdges = [];

        res.impacted_nodes.forEach((node, i) => {
          const isInput = filesArr.includes(node.file) || symbolsArr.includes(node.name) || symbolsArr.includes(node.qualified_name);
          const colorClass = node.is_test 
            ? 'bg-purple-500/10 dark:bg-purple-500/20 border border-purple-500/50 text-purple-700 dark:text-purple-300' 
            : isInput 
              ? 'bg-red-500/10 dark:bg-red-500/20 border-2 border-red-500 text-red-700 dark:text-red-300 font-bold' 
              : 'bg-blue-500/10 dark:bg-blue-500/20 border border-blue-500/50 text-blue-700 dark:text-blue-300';

          canvasNodes.push({
            id: node.qualified_name,
            data: { label: `${node.name}\n(${node.kind})` },
            position: { x: 100 + (node.hop * 250), y: 100 + (i % 8) * 100 },
            className: `${colorClass} rounded-lg p-2.5 text-xs w-[200px] text-center shadow-md transition-all cursor-pointer`,
          });

          // Draw an edge if hop is > 0 (connect backward to hop - 1)
          if (node.hop > 0) {
            // Find a node defined in the tier below to draw edge from
            const parentNode = res.impacted_nodes.find(p => p.hop === node.hop - 1);
            if (parentNode) {
              canvasEdges.push({
                id: `edge-${parentNode.qualified_name}-${node.qualified_name}`,
                source: parentNode.qualified_name,
                target: node.qualified_name,
                markerEnd: { type: MarkerType.ArrowClosed, color: '#3b82f6' },
                style: { stroke: '#3b82f6', strokeDasharray: '5,5', strokeWidth: 1.5 },
              });
            }
          }
        });

        setNodes(canvasNodes);
        setEdges(canvasEdges);
      }
    } catch (err) {
      console.error('Failed to compute impact', err);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen max-h-screen bg-background text-foreground overflow-hidden font-sans">
      {/* 1. LEFT SIDEBAR: Codebase Explorer & Domain Groupings */}
      <div className="w-80 border-r border-border bg-card flex flex-col flex-shrink-0">
        <div className="p-4 border-b border-border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Network className="w-5 h-5 text-primary animate-pulse" />
              <h2 className="font-bold text-lg tracking-tight bg-gradient-to-r from-primary to-indigo-500 bg-clip-text text-transparent">
                Codebase Explorer
              </h2>
            </div>
            {isLoading && <Activity className="w-4 h-4 text-primary animate-spin" />}
          </div>

          {/* Repo selector */}
          <select
            value={selectedRepo}
            onChange={(e) => setSelectedRepo(e.target.value)}
            className="w-full bg-background border border-border text-foreground rounded-md py-1.5 px-3 text-sm focus:outline-none focus:border-primary transition-colors"
          >
            {repos.map(r => (
              <option key={r.repo_name} value={r.repo_name}>
                {r.repo_name} ({r.files_indexed} files)
              </option>
            ))}
          </select>

          {/* Index lifecycle actions */}
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={handleReindex}
              disabled={!selectedRepo || isMutating}
              title="Rebuild this repository's index from scratch"
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isMutating
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <RefreshCw className="w-3.5 h-3.5" />}
              Reindex
            </button>
            <button
              onClick={handleDeleteIndex}
              disabled={!selectedRepo || isMutating}
              title="Delete this repository's entire index"
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-destructive/30 bg-background px-2.5 py-1.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Delete Index
            </button>
          </div>
          {actionError && (
            <p className="mt-2 text-[11px] text-destructive leading-snug">{actionError}</p>
          )}
        </div>

        {/* Tab Headers */}
        <div className="flex border-b border-border text-xs">
          <button
            onClick={() => setActiveTab('structure')}
            className={`flex-1 py-3 text-center border-b-2 font-medium transition-all ${
              activeTab === 'structure' ? 'border-primary text-foreground font-semibold' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            File Tree
          </button>
          <button
            onClick={() => setActiveTab('domain')}
            className={`flex-1 py-3 text-center border-b-2 font-medium transition-all ${
              activeTab === 'domain' ? 'border-primary text-foreground font-semibold' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            Business Domains
          </button>
          <button
            onClick={() => setActiveTab('impact')}
            className={`flex-1 py-3 text-center border-b-2 font-medium transition-all ${
              activeTab === 'impact' ? 'border-primary text-foreground font-semibold' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            Diff Impact
          </button>
        </div>

        {/* Tab Contents */}
        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
          {activeTab === 'structure' && (
            <div className="space-y-4">
              <div className="relative">
                <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-2.5" />
                <Input
                  placeholder="Search files..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9 bg-background border-border text-foreground text-sm focus-visible:ring-primary"
                />
              </div>

              <div className="space-y-1">
                {filteredFiles.map(file => (
                  <button
                    key={file.file}
                    onClick={() => handleFileClick(file)}
                    className={`w-full text-left px-3 py-2 rounded-md text-xs transition-colors flex items-center gap-2 ${
                      selectedFile?.file === file.file
                        ? 'bg-primary/10 text-primary font-semibold border-l-2 border-primary'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                    }`}
                  >
                    <FileCode className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="truncate flex-1">{file.file}</span>
                    {file.node_count > 0 && (
                      <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
                        {file.node_count}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'domain' && (
            <div className="space-y-3">
              {Object.keys(domainGroups).map(domainName => (
                <div key={domainName} className="space-y-1">
                  <div className="flex items-center gap-2 text-xs font-semibold text-primary bg-muted px-2.5 py-1.5 rounded">
                    <Layers className="w-3.5 h-3.5 text-primary" />
                    <span>{domainName}</span>
                    <span className="ml-auto text-[10px] text-muted-foreground">
                      {domainGroups[domainName].length} files
                    </span>
                  </div>
                  <div className="pl-4 space-y-0.5 border-l border-border">
                    {domainGroups[domainName].map(file => (
                      <button
                        key={file.file}
                        onClick={() => handleFileClick(file)}
                        className={`w-full text-left px-2 py-1.5 rounded text-[11px] transition-colors truncate block ${
                          selectedFile?.file === file.file
                            ? 'bg-primary/10 text-primary font-semibold'
                            : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
                        }`}
                      >
                        {file.file.split('/').pop()}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'impact' && (
            <DiffImpactPanel
              diffFilesText={diffFilesText}
              setDiffFilesText={setDiffFilesText}
              diffSymbolsText={diffSymbolsText}
              setDiffSymbolsText={setDiffSymbolsText}
              handleRunDiffImpact={handleRunDiffImpact}
              diffImpactResult={diffImpactResult}
            />
          )}
        </div>
      </div>

      {/* 2. CENTRAL PANEL: Interactive visual graph canvas (0 LLM cost) */}
      <div className="flex-1 min-w-0 h-full relative bg-background border-r border-border">
        <div className="absolute top-4 left-4 z-10 flex gap-2">
          <div className="bg-card/90 border border-border px-3 py-1.5 rounded-md text-xs flex items-center gap-2 backdrop-blur shadow-lg">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-foreground">
              Interactive Canvas (Double-click node to expand callers/callees)
            </span>
          </div>
        </div>

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleCliick}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          className="w-full h-full"
        >
          <Background color="currentColor" className="text-muted-foreground/15" gap={16} />
          <Controls className="bg-card border border-border text-foreground" />
          <MiniMap 
            nodeColor={() => 'var(--primary)'} 
            maskColor="rgba(0,0,0,0.15)" 
            className="bg-card border border-border"
          />
        </ReactFlow>
      </div>

      {/* 3. RIGHT SIDEBAR: Code entity details & Guided Code Tours */}
      <div className="w-96 border-l border-border bg-card flex flex-col flex-shrink-0 overflow-y-auto custom-scrollbar">
        {selectedNode ? (
          <div className="p-5 space-y-6">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded border ${
                  KIND_COLORS[selectedNode.kind] || DEFAULT_COLOR
                }`}>
                  {selectedNode.kind}
                </span>
                <span className="text-muted-foreground text-xs truncate flex-1">{selectedNode.file}</span>
              </div>
              <h3 className="text-xl font-bold tracking-tight text-foreground break-words mt-1">
                {selectedNode.name}
              </h3>
            </div>

            {/* Signature */}
            {selectedNode.signature && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Signature
                </h4>
                <pre className="p-3 bg-muted rounded-md border border-border text-xs font-mono overflow-x-auto text-primary">
                  {selectedNode.signature}
                </pre>
              </div>
            )}

            {/* Logical summary (Postgres FTS metadata / deterministic tour) */}
            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Logical Summary
              </h4>
              <p className="text-foreground text-xs leading-relaxed bg-muted/40 p-3 rounded-md border border-border">
                {selectedNode.docstring || (
                  <span className="text-muted-foreground italic">
                    No code documentation available. The node resides under the{' '}
                    <strong className="text-primary">{selectedNode.domain || 'Core'}</strong> logical domain.
                  </span>
                )}
              </p>
            </div>

            {/* Lines in source */}
            {selectedNode.line_start && (
              <div className="flex gap-4 border-t border-b border-border py-3 text-xs">
                <div>
                  <span className="text-muted-foreground">Line Start:</span>{' '}
                  <span className="font-semibold text-foreground">{selectedNode.line_start}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Line End:</span>{' '}
                  <span className="font-semibold text-foreground">{selectedNode.line_end}</span>
                </div>
              </div>
            )}

            {/* Callers, Callees and Impact metrics */}
            {selectedNode.kind !== 'file' && (
              <div className="space-y-3">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Graph Relationships
                </h4>
                <div className="grid grid-cols-2 gap-2 text-center text-xs">
                  <div className="p-2.5 bg-muted/60 rounded-lg border border-border">
                    <span className="text-muted-foreground block mb-0.5">Confidence</span>
                    <span className="font-semibold text-emerald-600 dark:text-emerald-400 flex items-center justify-center gap-1">
                      <ShieldCheck className="w-3.5 h-3.5" />
                      Deterministic
                    </span>
                  </div>
                  <div className="p-2.5 bg-muted/60 rounded-lg border border-border">
                    <span className="text-muted-foreground block mb-0.5">Language</span>
                    <span className="font-semibold text-foreground capitalize">
                      {selectedNode.language || 'csharp'}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
            <Info className="w-8 h-8 text-muted-foreground/60 mb-3" />
            <h3 className="font-semibold text-foreground mb-1">No Entity Selected</h3>
            <p className="text-xs max-w-xs leading-relaxed">
              Select a file or business domain on the left, or double-click nodes on the canvas to inspect detail panels.
            </p>
          </div>
        )}

        {/* Read-only project intelligence (brief / standards / modules) */}
        {selectedRepo && <ProjectIntelligencePanel repo={selectedRepo} />}

        {/* Guided Code Tour segment for Diff Impact */}
        {activeTab === 'impact' && diffImpactResult && (
          <div className="border-t border-border p-5 space-y-4 text-xs">
            <h4 className="font-bold text-foreground flex items-center gap-1.5">
              <GitBranch className="w-4 h-4 text-primary" />
              Guided Code Tour (Diff Path)
            </h4>

            {diffImpactResult.total_impacted_nodes > 0 ? (
              <div className="space-y-4">
                <p className="text-muted-foreground text-xs leading-relaxed">
                  The changes made to this diff propagate across the codebase. Following this sequence lists the call dependencies:
                </p>

                <div className="relative pl-6 space-y-4 border-l-2 border-border">
                  {diffImpactResult.affected_entry_points.slice(0, 5).map((node, i) => (
                    <div key={node.qualified_name} className="relative">
                      <span className="absolute -left-8 top-0.5 w-5 h-5 rounded-full bg-background border border-border text-[10px] font-bold flex items-center justify-center text-foreground">
                        {i + 1}
                      </span>
                      <div className="space-y-1">
                        <span className="font-semibold text-foreground break-all block">{node.name}</span>
                        <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                          <span>{node.kind}</span>
                          <span>•</span>
                          <span>Hop {node.hop}</span>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>

                {diffImpactResult.affected_tests.length > 0 && (
                  <div className="p-3 bg-purple-500/5 rounded border border-purple-500/20 text-purple-700 dark:text-purple-300">
                    <span className="font-bold block mb-1">🛡️ Recommended Tests to Run</span>
                    <ul className="list-disc pl-4 space-y-1">
                      {diffImpactResult.affected_tests.slice(0, 3).map(test => (
                        <li key={test.qualified_name} className="break-all">{test.name}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground italic">No impacted paths found. Ensure files and symbols entered match your index.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
