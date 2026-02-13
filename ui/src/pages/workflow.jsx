import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Button,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  Alert
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  MoreVert as MoreVertIcon,
  Close as CloseIcon,
  Save as SaveIcon
} from '@mui/icons-material';
import {
  FormControl,
  InputLabel,
  Select
} from '@mui/material';
import WorkflowEditor from '../components/workflow/WorkflowEditor.jsx';
import { validateWorkflow } from '../utils/workflowValidation.js';
import { syncWorkflowToSchedules, cleanOrphanedEdges } from '../utils/workflowScheduleSync.js';
import agentApiClient from '../services/agentApiClient.js';

// Debounce hook for search optimization
const useDebounce = (value, delay) => {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
};

function Workflow() {
  const [workflows, setWorkflows] = useState([]);
  const [showDialog, setShowDialog] = useState(false);
  const [showWorkflowEditor, setShowWorkflowEditor] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const debouncedSearchTerm = useDebounce(searchTerm, 300);
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [workflowToDelete, setWorkflowToDelete] = useState(null);
  const [workflowName, setWorkflowName] = useState('');
  const [workflowType, setWorkflowType] = useState('workflow');
  const [currentWorkflowData, setCurrentWorkflowData] = useState(null);
  const [workflowEditorData, setWorkflowEditorData] = useState({ nodes: [], edges: [] });
  const [messages, setMessages] = useState([]);
  const workflowEditorRef = useRef(null);

  useEffect(() => {
    loadWorkflows();
  }, []);

  const loadWorkflows = async () => {
    try {
      const data = await agentApiClient.listWorkflows();
      setWorkflows(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Error loading workflows:', error);
      setWorkflows([]);
      if (error.code === 'ERR_NETWORK' || error.message.includes('Network Error')) {
        showMessage('Cannot connect to Agent API at http://localhost:8000. Please start the API server.', 'error');
      } else {
        showMessage('Failed to load workflows: ' + (error.response?.data?.detail || error.message), 'error');
      }
    }
  };

  const showMessage = (msg, type = 'info') => {
    const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setMessages(prev => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setMessages(prev => prev.filter(m => m.id !== id));
    }, 3000);
  };

  const removeMessage = (id) => {
    setMessages(prev => prev.filter(m => m.id !== id));
  };





  const handleDeleteWorkflow = async (workflow) => {
    setWorkflowToDelete(workflow);
    setDeleteDialogOpen(true);
    handleMenuClose();
  };

  const confirmDelete = async () => {
    if (!workflowToDelete) return;

    try {
      await agentApiClient.deleteWorkflow(workflowToDelete.name);
      const updatedWorkflows = workflows.filter(w => w.id !== workflowToDelete.id);
      setWorkflows(updatedWorkflows);

      await removeWorkflowSchedules(workflowToDelete.id);

      showMessage('Workflow deleted', 'success');
    } catch (error) {
      console.error('Error deleting workflow:', error);
      showMessage('Failed to delete workflow', 'error');
    } finally {
      setDeleteDialogOpen(false);
      setWorkflowToDelete(null);
    }
  };

  // Remove schedules associated with a workflow
  const removeWorkflowSchedules = async (workflowId) => {
    try {
      // Load existing schedules
      let existingSchedules = [];
      if (window.electronAPI && window.electronAPI.loadSchedules) {
        existingSchedules = await window.electronAPI.loadSchedules();
      } else {
        const data = localStorage.getItem('oncall-schedules');
        existingSchedules = data ? JSON.parse(data) : [];
      }

      // Remove schedules for this workflow
      const updatedSchedules = existingSchedules.filter(s => s.workflowId !== workflowId);

      // Save updated schedules
      if (window.electronAPI && window.electronAPI.saveSchedules) {
        await window.electronAPI.saveSchedules(updatedSchedules);
      } else {
        localStorage.setItem('oncall-schedules', JSON.stringify(updatedSchedules));
      }

      console.log(`Removed schedules for workflow: ${workflowId}`);
    } catch (error) {
      console.error('Error removing workflow schedules:', error);
    }
  };

  const handleMenuOpen = (event, workflow) => {
    setAnchorEl(event.currentTarget);
    setSelectedWorkflow(workflow);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setSelectedWorkflow(null);
  };

  const openEditDialog = (workflow) => {
    setEditingWorkflow(workflow);
    setWorkflowName(workflow.name);
    setWorkflowType(workflow.type || 'workflow');
    setCurrentWorkflowData(workflow);

    // Set the workflow editor data to the specific workflow's nodes and edges
    setWorkflowEditorData({
      nodes: workflow.nodes || [],
      edges: workflow.edges || []
    });
    setShowWorkflowEditor(true);
    handleMenuClose();
  };

  const openAddDialog = () => {
    setWorkflowName('');
    setWorkflowType('workflow');
    setCurrentWorkflowData(null);
    setShowDialog(true);
  };

  const handleProceedToEditor = () => {
    if (!workflowName) {
      showMessage('Please enter workflow name', 'error');
      return;
    }
    // For new workflows, start with empty editor data
    setWorkflowEditorData({ nodes: [], edges: [] });
    setShowDialog(false);
    setShowWorkflowEditor(true);
  };

  const handleSaveWorkflowData = async (nodes, edges) => {
    try {

      if (!workflowName) {
        showMessage('Please enter workflow name', 'error');
        return;
      }

      // Validate that workflow has at least one node
      if (!nodes || nodes.length === 0) {
        showMessage('Cannot save empty workflow. Please add at least one node.', 'error');
        return;
      }

      // Validate workflow based on type
      const validation = validateWorkflow(workflowType, nodes, edges);
      if (!validation.isValid) {
        showMessage(validation.error, 'error');
        return;
      }

      // Create a new workflow object with the design data
      let newWorkflow = {
        id: currentWorkflowData?.id || Date.now().toString(),
        name: workflowName,
        type: workflowType,
        nodes: nodes,
        edges: edges,
        createdAt: currentWorkflowData?.createdAt || new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };

      // Extract cron schedule from scheduler nodes (for Scheduler Management)
      const schedulerNode = nodes.find(n => n.type === 'scheduler');
      if (schedulerNode && schedulerNode.data?.cronExpression) {
        newWorkflow.schedule = schedulerNode.data.cronExpression;
        newWorkflow.enabled = schedulerNode.data.enabled !== false;
      }

      // Clean up any orphaned edges (edges referencing non-existent nodes)
      newWorkflow = cleanOrphanedEdges(newWorkflow);

      // Save or update workflow via API
      const existingWorkflow = workflows.find(w => w.id === newWorkflow.id);

      if (existingWorkflow) {
        await agentApiClient.updateWorkflow(existingWorkflow.name, newWorkflow);
      } else {
        await agentApiClient.createWorkflow(newWorkflow);
      }

      // Update local state
      let existingWorkflows = [...workflows];
      const existingIndex = existingWorkflows.findIndex(w => w.id === newWorkflow.id);

      if (existingIndex >= 0) {
        existingWorkflows[existingIndex] = newWorkflow;
      } else {
        existingWorkflows.push(newWorkflow);
      }

      setWorkflows(existingWorkflows);

      setShowWorkflowEditor(false);
      setCurrentWorkflowData(null);
      setWorkflowName('');
      setWorkflowType('workflow');
      showMessage('Workflow saved successfully', 'success');
    } catch (error) {
      console.error('Error saving workflow:', error);
      showMessage('Failed to save workflow', 'error');
    }
  };

  const handleCancelEditor = () => {
    setShowWorkflowEditor(false);
    setCurrentWorkflowData(null);
    setWorkflowName('');
    setWorkflowType('workflow');
  };



  // Memoize filtered workflows to prevent unnecessary re-renders
  const filteredWorkflows = useMemo(() => {
    const searchLower = debouncedSearchTerm.toLowerCase();
    return workflows.filter(workflow => {
      const name = (workflow.name || '').toLowerCase();
      return name.includes(searchLower);
    });
  }, [workflows, debouncedSearchTerm]);

  // Memoize date formatter to avoid recreation on each render
  const formatDate = useCallback((dateString) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      {showWorkflowEditor ? (
        <div className="h-screen flex flex-col relative">
          {messages.length > 0 && (
            <div style={{
              position: 'absolute',
              top: '70px',
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 1000,
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              width: 'fit-content'
            }}>
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={`connection-message ${m.type}`}
                  style={{ position: 'relative', cursor: 'pointer' }}
                  onClick={() => removeMessage(m.id)}
                >
                  {m.type === 'success' ? '✅' : m.type === 'error' ? '❌' : 'ℹ️'} {m.msg}
                </div>
              ))}
            </div>
          )}
          {/* Editor Header */}
          <div className="bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center">
            <div className="flex-1">
              <h2 className="text-xl font-semibold text-gray-900">{workflowName || 'New Workflow'}</h2>
            </div>
            <div className="flex">
              <Button
                variant="outlined"
                startIcon={<CloseIcon />}
                onClick={handleCancelEditor}
                sx={{ marginRight: '0.5rem' }}
              >
                Cancel
              </Button>
              <Button
                variant="contained"
                startIcon={<SaveIcon />}
                onClick={() => {
                  if (workflowEditorRef.current) {
                    const { nodes, edges } = workflowEditorRef.current.getWorkflowData();
                    handleSaveWorkflowData(nodes, edges);
                  } else {
                    showMessage('Workflow editor not ready', 'error');
                  }
                }}
              >
                Save Workflow
              </Button>
            </div>
          </div>

          {/* Workflow Editor */}
          <div className="flex-1">
            <WorkflowEditor
              ref={workflowEditorRef}
              initialNodes={workflowEditorData?.nodes}
              initialEdges={workflowEditorData?.edges}
            />
          </div>
        </div>
      ) : (
        <div className="p-8">
          <div className="max-w-7xl mx-auto">
            {/* Header */}
            <div className="mb-6 flex justify-between items-center">
              <div>
                <h1 className="text-3xl font-semibold text-gray-900 mb-2">
                  Workflow Management
                </h1>
                <p className="text-gray-600">
                  Manage and organize your automated workflows
                </p>
              </div>

              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openAddDialog}
              >
                Add Workflow
              </Button>
            </div>

            {/* Search */}
            <div className="mb-6">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="col-span-1">
                  <TextField
                    fullWidth
                    label="Search workflows"
                    variant="outlined"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    size="small"
                  />
                </div>
              </div>
            </div>

            {/* Workflow Table */}
            {filteredWorkflows.length === 0 ? (
              <Alert severity="info">
                {workflows.length === 0
                  ? "No workflows created yet. Click 'Add Workflow' to create your first workflow."
                  : "No workflows match your search."
                }
              </Alert>
            ) : (
              <div>
                <p className="text-sm text-gray-600 mb-4">
                  Showing {filteredWorkflows.length} of {workflows.length} workflows
                </p>

                <TableContainer component={Paper} className="shadow-md">
                  <Table>
                    <TableHead className="bg-gradient-to-r from-purple-600 to-purple-800">
                      <TableRow>
                        <TableCell className="text-white font-semibold">Name</TableCell>
                        <TableCell className="text-white font-semibold">Type</TableCell>
                        <TableCell className="text-white font-semibold">Created</TableCell>
                        <TableCell className="text-white font-semibold">Last Updated</TableCell>
                        <TableCell align="right" className="text-white font-semibold">Actions</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {filteredWorkflows.map((workflow) => (
                        <TableRow key={workflow.id} hover className="hover:bg-gray-50 transition-colors">
                          <TableCell>
                            <span className="font-semibold text-gray-900">
                              {workflow.name}
                            </span>
                          </TableCell>
                          <TableCell>
                            <Chip
                              label={workflow.type === 'agent' ? 'Agent' : 'Workflow'}
                              color={workflow.type === 'agent' ? 'secondary' : 'primary'}
                              size="small"
                            />
                          </TableCell>
                          <TableCell>
                            <span className="text-sm text-gray-600">
                              {formatDate(workflow.createdAt)}
                            </span>
                          </TableCell>
                          <TableCell>
                            <span className="text-sm text-gray-600">
                              {formatDate(workflow.updatedAt)}
                            </span>
                          </TableCell>
                          <TableCell align="right">
                            <IconButton
                              size="small"
                              onClick={(e) => handleMenuOpen(e, workflow)}
                            >
                              <MoreVertIcon />
                            </IconButton>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </div>
            )}

            {/* Action Menu */}
            <Menu
              anchorEl={anchorEl}
              open={Boolean(anchorEl)}
              onClose={handleMenuClose}
            >
              <MenuItem onClick={() => openEditDialog(selectedWorkflow)}>
                <div className="flex items-center gap-2">
                  <EditIcon fontSize="small" />
                  <span>Edit</span>
                </div>
              </MenuItem>
              <MenuItem onClick={() => handleDeleteWorkflow(selectedWorkflow)} className="text-red-600">
                <div className="flex items-center gap-2">
                  <DeleteIcon fontSize="small" />
                  <span>Delete</span>
                </div>
              </MenuItem>
            </Menu>

            {/* Delete Confirmation Dialog */}
            <Dialog
              open={deleteDialogOpen}
              onClose={() => setDeleteDialogOpen(false)}
            >
              <DialogTitle>Confirm Delete</DialogTitle>
              <DialogContent>
                <p className="text-gray-700">
                  Are you sure you want to delete the workflow "{workflowToDelete?.name}"?
                  This action cannot be undone.
                </p>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
                <Button onClick={confirmDelete} color="error" variant="contained">
                  Delete
                </Button>
              </DialogActions>
            </Dialog>

            {/* Add Workflow Dialog - Name and Schedule */}
            <Dialog
              open={showDialog}
              onClose={() => setShowDialog(false)}
              maxWidth="sm"
              fullWidth
            >
              <DialogTitle className="text-xl font-semibold">
                Create New Workflow
              </DialogTitle>
              <DialogContent sx={{ pt: 3 }}>
                <TextField
                  fullWidth
                  label="Workflow Name"
                  value={workflowName}
                  onChange={(e) => setWorkflowName(e.target.value)}
                  placeholder="e.g., daily-report"
                  required
                  autoFocus
                  sx={{ mt: 1 }}
                />
                <FormControl fullWidth sx={{ mt: 2 }}>
                  <InputLabel id="workflow-type-label">Type</InputLabel>
                  <Select
                    labelId="workflow-type-label"
                    value={workflowType}
                    label="Type"
                    onChange={(e) => setWorkflowType(e.target.value)}
                    native
                  >
                    <option value="workflow">Workflow</option>
                    <option value="agent">Agent</option>
                  </Select>
                </FormControl>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setShowDialog(false)}>Cancel</Button>
                <Button onClick={handleProceedToEditor} variant="contained">
                  Continue to Editor
                </Button>
              </DialogActions>
            </Dialog>
          </div>
        </div>
      )}
    </div>
  );
}

export default Workflow;
