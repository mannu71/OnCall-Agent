import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Plus, Edit2, Trash2, MoreVertical, X, Save, Play, Search } from 'lucide-react';
import WorkflowEditor from '../components/workflow/WorkflowEditor.jsx';
import { validateWorkflow } from '../utils/workflowValidation.js';
import agentApiClient from '../services/agentApiClient.js';
import { useScheduler } from '../context/SchedulerContext';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';

function Workflow() {
  const {
    schedules: workflows,
    loadSchedules,
    deleteSchedule,
    triggerWorkflow,
    formatTime
  } = useScheduler();
  const { isWorkflowRunning } = useWorkflowStatus();

  const [showDialog, setShowDialog] = useState(false);
  const [showWorkflowEditor, setShowWorkflowEditor] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const [workflowName, setWorkflowName] = useState('');
  const [workflowType, setWorkflowType] = useState('workflow');
  const [currentWorkflowData, setCurrentWorkflowData] = useState(null);
  const [editorKey, setEditorKey] = useState(0);
  const [messages, setMessages] = useState([]);

  const workflowEditorRef = useRef(null);

  // Sync with API on mount
  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const removeMessage = useCallback((id) => {
    setMessages(prev => prev.filter(m => m.id !== id));
  }, []);

  const showMessage = useCallback((msg, type = 'info') => {
    const id = Math.random().toString(36).substring(2, 11);
    setMessages(prev => [...prev, { id, msg, type }]);
    setTimeout(() => removeMessage(id), 4000);
  }, [removeMessage]);

  const getMessageClasses = (type) => {
    if (type === 'error') return 'bg-red-50 border-red-200 text-red-800';
    if (type === 'warning') return 'bg-yellow-50 border-yellow-200 text-yellow-800';
    if (type === 'success') return 'bg-green-50 border-green-200 text-green-800';
    return 'bg-blue-50 border-blue-200 text-blue-800';
  };

  const handleExecute = async (workflow) => {
    // Prevent duplicate execution if already running
    if (isWorkflowRunning(workflow.name)) {
      showMessage(`Workflow '${workflow.name}' is already running`, 'warning');
      return;
    }

    try {
      await triggerWorkflow(workflow.name);
      showMessage(`Workflow '${workflow.name}' started`, 'success');
    } catch (error) {
      showMessage(`Failed to start workflow: ${error.message}`, 'error');
    }
  };

  const confirmDelete = async () => {
    if (!selectedWorkflow) return;
    try {
      await deleteSchedule(selectedWorkflow.name);
      showMessage('Workflow deleted successfully', 'success');
    } catch (error) {
      console.error('Error deleting workflow:', error);
      showMessage('Failed to delete workflow', 'error');
    } finally {
      setDeleteDialogOpen(false);
      setSelectedWorkflow(null);
    }
  };

  const openEditDialog = async (workflow) => {
    try {
      // Fetch fresh data for editor
      const fresh = await agentApiClient.getWorkflow(workflow.name);
      setWorkflowName(fresh.name);
      setWorkflowType(fresh.type || 'workflow');
      setCurrentWorkflowData(fresh);
      setEditorKey(prev => prev + 1);
      setShowWorkflowEditor(true);
    } catch (error) {
      console.error('Error loading workflow:', error);
      showMessage('Failed to load workflow details', 'error');
    }
  };

  const handleProceedToEditor = () => {
    if (!workflowName.trim()) {
      showMessage('Please enter workflow name', 'error');
      return;
    }
    setCurrentWorkflowData(null);
    setEditorKey(prev => prev + 1);
    setShowDialog(false);
    setShowWorkflowEditor(true);
  };

  const handleSaveWorkflowData = async () => {
    if (!workflowEditorRef.current) return;

    const { nodes, edges } = workflowEditorRef.current.getWorkflowData();

    if (!nodes?.length) {
      showMessage('Workflow must have at least one node', 'error');
      return;
    }

    const validation = validateWorkflow(workflowType, nodes, edges);
    if (!validation.isValid) {
      showMessage(validation.error, 'error');
      return;
    }

    try {
      const {
        schedule, enabled, startTime, recurrence, createdAt, updatedAt, ...baseData
      } = currentWorkflowData || {};

      const payload = {
        ...baseData,
        name: workflowName,
        type: workflowType,
        nodes,
        edges,
        updatedAt: new Date().toISOString()
      };

      if (currentWorkflowData) {
        await agentApiClient.updateWorkflow(currentWorkflowData.name, payload);
      } else {
        await agentApiClient.createWorkflow(payload);
      }

      await loadSchedules();
      setShowWorkflowEditor(false);
      showMessage('Workflow saved successfully', 'success');
    } catch (error) {
      showMessage(`Save failed: ${error.message}`, 'error');
    }
  };

  const filteredWorkflows = useMemo(() => {
    const term = searchTerm.toLowerCase();
    return workflows.filter(w => w.name.toLowerCase().includes(term));
  }, [workflows, searchTerm]);



  if (showWorkflowEditor) {
    return (
      <div className="h-screen flex flex-col">
        <div className="border-b bg-background">
          <div className="flex items-center gap-4 px-4 py-3">
            <Input
              value={workflowName}
              onChange={(e) => setWorkflowName(e.target.value)}
              className="flex-1 text-xl font-semibold"
            />
            <Button variant="outline" onClick={() => setShowWorkflowEditor(false)}>
              <X className="w-4 h-4 mr-2" />
              Cancel
            </Button>
            <Button onClick={handleSaveWorkflowData}>
              <Save className="w-4 h-4 mr-2" />
              Save
            </Button>
          </div>
        </div>

        {messages.map(m => (
          <div
            key={m.id}
            className={`absolute top-[70px] left-1/2 -translate-x-1/2 z-[2000] px-4 py-3 rounded border ${getMessageClasses(m.type)}`}
          >
            {m.msg}
          </div>
        ))}

        <div className="flex-1 relative">
          <WorkflowEditor
            key={editorKey}
            ref={workflowEditorRef}
            workflowName={workflowName}
            initialNodes={currentWorkflowData?.nodes || []}
            initialEdges={currentWorkflowData?.edges || []}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-4xl font-bold">Workflows</h1>
          <p className="text-muted-foreground mt-1">Design and manage agentic processes</p>
        </div>
        <Button onClick={() => { setWorkflowName(''); setShowDialog(true); }}>
          <Plus className="w-4 h-4 mr-2" />
          New Workflow
        </Button>
      </div>

      <div className="mb-4">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search workflows..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      <div className="border rounded-lg bg-white">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50/50">
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Name</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Type</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Schedule</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Status</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="text-sm">
            {filteredWorkflows.map((w) => (
              <TableRow key={w.name}>
                <TableCell className="font-semibold text-slate-900">{w.name}</TableCell>
                <TableCell className="text-slate-600">
                  <Badge variant="outline">{w.type || 'workflow'}</Badge>
                </TableCell>
                <TableCell>
                  {w.startTime ? (
                    <div>
                      <span className="text-sm">{formatTime(w.startTime)}</span>
                      <span className="text-xs text-muted-foreground ml-2">
                        ({w.schedule ? w.schedule.split(' ').slice(0, 2).join(':') + ' UTC' : 'Manual'})
                      </span>
                    </div>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      {w.schedule || 'Manual'}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={w.enabled ? 'success' : 'secondary'}>
                    {w.enabled ? 'Active' : 'Disabled'}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="icon" variant="ghost">
                        <MoreVertical className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => openEditDialog(w)}>
                        <Edit2 className="w-4 h-4 mr-2" />
                        Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleExecute(w)}>
                        <Play className="w-4 h-4 mr-2" />
                        Run Now
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => {
                          setSelectedWorkflow(w);
                          setDeleteDialogOpen(true);
                        }}
                        className="text-red-600"
                      >
                        <Trash2 className="w-4 h-4 mr-2" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
            {filteredWorkflows.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-6 text-muted-foreground">
                  No workflows found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Delete</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{selectedWorkflow?.name}"?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create Workflow</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label htmlFor="workflow-name" className="text-sm font-medium">Name</label>
              <Input
                id="workflow-name"
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="workflow-type" className="text-sm font-medium">Type</label>
              <select
                id="workflow-type"
                value={workflowType}
                onChange={(e) => setWorkflowType(e.target.value)}
                className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="workflow">Standard Workflow</option>
                <option value="agent">Agentic Process</option>
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDialog(false)}>Cancel</Button>
            <Button onClick={handleProceedToEditor}>Create</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {messages.map(m => (
        <div
          key={m.id}
          className={`fixed bottom-5 right-5 min-w-[250px] px-4 py-3 rounded border ${getMessageClasses(m.type)}`}
        >
          {m.msg}
        </div>
      ))}
    </div>
  );
}

// End of Workflow components

export default Workflow;
