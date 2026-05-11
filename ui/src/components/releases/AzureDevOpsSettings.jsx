import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import {
  Settings,
  Trash2,
  CheckCircle2,
  XCircle,
  Loader2,
  Eye,
  EyeOff,
  TestTube,
  KeyRound
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import agentApiClient from '@/services/agentApiClient';

/**
 * AzureDevOpsSettings Component
 *
 * Manages a single global Azure DevOps Personal Access Token.
 * Organization and project are specified per-request in the wizard.
 */
const AzureDevOpsSettings = ({ onSave }) => {
  const [isConfigured, setIsConfigured] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // PAT form
  const [showDialog, setShowDialog] = useState(false);
  const [pat, setPat] = useState('');
  const [showPat, setShowPat] = useState(false);

  // Test connection state
  const [showTestDialog, setShowTestDialog] = useState(false);
  const [testOrganization, setTestOrganization] = useState('');
  const [testProject, setTestProject] = useState('');
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState(null);

  // Check whether a PAT is configured on mount
  useEffect(() => {
    checkConfigured();
  }, []);

  const checkConfigured = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentApiClient.getAzureDevOpsOrganizations();
      // Backend returns {organizations: ["_default"]} when a PAT is stored
      setIsConfigured((response.organizations || []).length > 0);
    } catch (err) {
      console.error('Error checking PAT status:', err);
      setError('Failed to check PAT configuration');
    } finally {
      setLoading(false);
    }
  }, []);

  // Save PAT
  const handleSave = useCallback(async () => {
    if (!pat.trim()) {
      setError('Personal Access Token is required');
      return;
    }
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await agentApiClient.saveAzureDevOpsCredentials(pat.trim());
      setSuccess('PAT saved successfully');
      setShowDialog(false);
      setPat('');
      setIsConfigured(true);
      if (onSave) onSave();
    } catch (err) {
      console.error('Error saving PAT:', err);
      setError(err.response?.data?.message || err.message || 'Failed to save PAT');
    } finally {
      setLoading(false);
    }
  }, [pat, onSave]);

  // Delete PAT
  const handleDelete = useCallback(async () => {
    if (!globalThis.confirm('Remove the stored Azure DevOps PAT?')) return;
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await agentApiClient.deleteAzureDevOpsCredentials();
      setSuccess('PAT removed');
      setIsConfigured(false);
    } catch (err) {
      console.error('Error removing PAT:', err);
      setError(err.response?.data?.message || err.message || 'Failed to remove PAT');
    } finally {
      setLoading(false);
    }
  }, []);

  // Test connection
  const handleTestConnection = useCallback(async () => {
    if (!testOrganization.trim()) {
      setTestResult({ success: false, message: 'Organization is required' });
      return;
    }
    if (!testProject.trim()) {
      setTestResult({ success: false, message: 'Project is required' });
      return;
    }
    setTestLoading(true);
    setTestResult(null);
    try {
      const response = await agentApiClient.testAzureDevOpsConnection(
        testOrganization.trim(),
        testProject.trim()
      );
      setTestResult({ success: true, message: response.message || 'Connection successful!' });
    } catch (err) {
      console.error('Error testing connection:', err);
      setTestResult({
        success: false,
        message: err.response?.data?.message || err.message || 'Connection failed'
      });
    } finally {
      setTestLoading(false);
    }
  }, [testOrganization, testProject]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="border-b border-slate-100">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-xl font-bold text-slate-900 flex items-center gap-2">
                <Settings className="w-5 h-5 text-slate-600" />
                Azure DevOps Settings
              </CardTitle>
              <CardDescription className="mt-1">
                Store your Personal Access Token to enable work item search and release creation
              </CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-6">
          {success && (
            <Alert className="mb-6 border-green-200 bg-green-50">
              <CheckCircle2 className="h-4 w-4 text-green-600" />
              <AlertDescription className="text-green-800">{success}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert className="mb-6 border-red-200 bg-red-50">
              <XCircle className="h-4 w-4 text-red-600" />
              <AlertDescription className="text-red-800">{error}</AlertDescription>
            </Alert>
          )}

          {loading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-slate-400 animate-spin" />
            </div>
          )}

          {!loading && isConfigured && (
            <div className="flex items-center justify-between p-4 border border-slate-200 rounded-lg">
              <div className="flex items-center gap-3">
                <KeyRound className="w-5 h-5 text-green-600" />
                <div>
                  <p className="font-medium text-slate-900">PAT Configured</p>
                  <p className="text-sm text-slate-500">Personal Access Token is stored securely</p>
                </div>
                <Badge variant="outline" className="text-green-700 border-green-300">Active</Badge>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => { setTestOrganization(''); setTestProject(''); setTestResult(null); setShowTestDialog(true); }}
                  variant="outline"
                  size="sm"
                >
                  <TestTube className="w-4 h-4 mr-2" />
                  Test
                </Button>
                <Button
                  onClick={() => { setPat(''); setShowPat(false); setError(null); setShowDialog(true); }}
                  variant="outline"
                  size="sm"
                >
                  Update PAT
                </Button>
                <Button
                  onClick={handleDelete}
                  variant="outline"
                  size="sm"
                  className="text-red-600 border-red-300 hover:bg-red-50"
                  disabled={loading}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          )}

          {!loading && !isConfigured && (
            <div className="text-center py-12 border-2 border-dashed border-slate-200 rounded-lg">
              <KeyRound className="w-12 h-12 mx-auto mb-3 text-slate-400" />
              <p className="text-slate-600 mb-2">No PAT configured</p>
              <p className="text-sm text-slate-500 mb-4">
                Add your Azure DevOps Personal Access Token to get started
              </p>
              <Button
                onClick={() => { setPat(''); setShowPat(false); setError(null); setShowDialog(true); }}
              >
                Configure PAT
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* PAT Dialog */}
      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Azure DevOps PAT</DialogTitle>
            <DialogDescription>
              Enter your Personal Access Token. It will be encrypted and stored securely.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            {error && (
              <Alert className="border-red-200 bg-red-50">
                <XCircle className="h-4 w-4 text-red-600" />
                <AlertDescription className="text-red-800 text-sm">{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="pat">Personal Access Token *</Label>
              <div className="relative">
                <Input
                  id="pat"
                  type={showPat ? 'text' : 'password'}
                  placeholder="Enter your PAT"
                  value={pat}
                  onChange={(e) => setPat(e.target.value)}
                  disabled={loading}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPat(!showPat)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showPat ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-slate-500">
                Requires Work Items (Read), Code (Read), and Build (Read) permissions
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowDialog(false); setError(null); }} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={loading}>
              {loading ? (
                <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Saving...</>
              ) : (
                <><CheckCircle2 className="w-4 h-4 mr-2" />Save PAT</>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Test Connection Dialog */}
      <Dialog open={showTestDialog} onOpenChange={setShowTestDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Test Connection</DialogTitle>
            <DialogDescription>
              Verify the stored PAT can connect to a specific organization and project
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            {testResult && (
              <Alert className={testResult.success ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'}>
                {testResult.success
                  ? <CheckCircle2 className="h-4 w-4 text-green-600" />
                  : <XCircle className="h-4 w-4 text-red-600" />}
                <AlertDescription className={testResult.success ? 'text-green-800' : 'text-red-800'}>
                  {testResult.message}
                </AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="testOrg">Organization *</Label>
              <Input
                id="testOrg"
                placeholder="my-organization"
                value={testOrganization}
                onChange={(e) => setTestOrganization(e.target.value)}
                disabled={testLoading}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="testProject">Project *</Label>
              <Input
                id="testProject"
                placeholder="my-project"
                value={testProject}
                onChange={(e) => setTestProject(e.target.value)}
                disabled={testLoading}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowTestDialog(false); setTestResult(null); }} disabled={testLoading}>
              Close
            </Button>
            <Button onClick={handleTestConnection} disabled={testLoading}>
              {testLoading ? (
                <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Testing...</>
              ) : (
                <><TestTube className="w-4 h-4 mr-2" />Test Connection</>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

AzureDevOpsSettings.propTypes = {
  onSave: PropTypes.func
};

AzureDevOpsSettings.defaultProps = {
  onSave: null
};

export default AzureDevOpsSettings;
