import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogDescription
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Eye,
    EyeOff,
    Shield,
    AlertCircle,
    ExternalLink,
    Info
} from 'lucide-react';
import { getProviderSchema } from '../../services/modelKeyService';

const ModelKeyDialog = ({
    open,
    onClose,
    onSave,
    editingProvider,
    existingData,
    availableProviders
}) => {
    const [formData, setFormData] = useState({});
    const [providerSchema, setProviderSchema] = useState(null);
    const [selectedProvider, setSelectedProvider] = useState(editingProvider || '');
    const [showFields, setShowFields] = useState({});
    const [validationErrors, setValidationErrors] = useState(null);
    const [loading, setLoading] = useState(false);

    // Load provider schema when provider changes
    useEffect(() => {
        if (selectedProvider) {
            loadProviderSchema(selectedProvider);
        }
    }, [selectedProvider]);

    // Initialize form data when editing
    useEffect(() => {
        if (editingProvider && existingData) {
            setSelectedProvider(editingProvider);
            setFormData(existingData);
        } else if (!editingProvider) {
            setFormData({});
            setSelectedProvider('');
        }
    }, [editingProvider, existingData, open]);

    const loadProviderSchema = async (provider) => {
        try {
            const schema = await getProviderSchema(provider);
            setProviderSchema(schema);
        } catch (error) {
            console.error('Failed to load provider schema:', error);
            setProviderSchema(null);
        }
    };

    const handleProviderChange = (provider) => {
        setSelectedProvider(provider);
        setFormData({ provider });
        setValidationErrors(null);
    };

    const handleFieldChange = (fieldName, value) => {
        setFormData(prev => ({
            ...prev,
            [fieldName]: value
        }));
        // Clear validation errors when user starts typing
        if (validationErrors) {
            setValidationErrors(null);
        }
    };

    const toggleFieldVisibility = (fieldName) => {
        setShowFields(prev => ({
            ...prev,
            [fieldName]: !prev[fieldName]
        }));
    };

    const handleSave = async () => {
        setLoading(true);
        setValidationErrors(null);
        
        try {
            const dataToSave = {
                provider: selectedProvider,
                ...formData
            };
            
            await onSave(dataToSave);
            onClose();
        } catch (error) {
            // Check if error response contains validation details
            if (error.message && error.message.includes('detail')) {
                try {
                    const errorData = JSON.parse(error.message.split('detail')[1]);
                    setValidationErrors(errorData);
                } catch (e) {
                    setValidationErrors({
                        message: error.message,
                        errors: []
                    });
                }
            } else {
                setValidationErrors({
                    message: error.message || 'Failed to save credentials',
                    errors: []
                });
            }
        } finally {
            setLoading(false);
        }
    };

    const renderFieldInput = (field) => {
        const isSecret = field.field_type === 'secret';
        const fieldValue = formData[field.name] || '';
        const isVisible = showFields[field.name];

        return (
            <div key={field.name} className="space-y-2">
                <Label htmlFor={`field-${field.name}`} className="flex items-center gap-2">
                    {field.display_name}
                    {field.required && (
                        <Badge variant="destructive" className="text-xs px-1 py-0">
                            Required
                        </Badge>
                    )}
                    {!field.required && (
                        <span className="text-xs text-muted-foreground">(optional)</span>
                    )}
                </Label>
                
                <div className="relative">
                    <Input
                        id={`field-${field.name}`}
                        value={fieldValue}
                        onChange={(e) => handleFieldChange(field.name, e.target.value)}
                        type={isSecret && !isVisible ? 'password' : 'text'}
                        placeholder={field.example || `Enter ${field.display_name.toLowerCase()}`}
                        className={isSecret ? 'pr-10' : ''}
                    />
                    {isSecret && (
                        <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            onClick={() => toggleFieldVisibility(field.name)}
                            className="absolute right-0 top-0 h-full"
                        >
                            {isVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </Button>
                    )}
                </div>
                
                <p className="text-xs text-muted-foreground">
                    {field.description}
                </p>
                
                {editingProvider && isSecret && fieldValue && (
                    <p className="text-xs text-amber-600">
                        Leave unchanged to keep existing value
                    </p>
                )}
            </div>
        );
    };

    return (
        <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
            <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>
                        {editingProvider ? `Edit Credentials: ${editingProvider}` : 'Configure Provider Credentials'}
                    </DialogTitle>
                    <DialogDescription>
                        {providerSchema?.description || 'Configure API credentials for your provider'}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4 mt-4">
                    {/* Provider Selection */}
                    {!editingProvider && (
                        <div className="space-y-2">
                            <Label htmlFor="provider-select">Provider</Label>
                            <Select
                                value={selectedProvider}
                                onValueChange={handleProviderChange}
                            >
                                <SelectTrigger id="provider-select">
                                    <SelectValue placeholder="Select a provider" />
                                </SelectTrigger>
                                <SelectContent>
                                    {availableProviders.map((provider) => (
                                        <SelectItem key={provider.value} value={provider.value}>
                                            {provider.icon} {provider.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    )}

                    {/* Validation Errors */}
                    {validationErrors && (
                        <Alert variant="destructive">
                            <AlertCircle className="h-4 w-4" />
                            <AlertDescription>
                                <div className="font-medium mb-2">
                                    {validationErrors.message || 'Validation failed'}
                                </div>
                                {validationErrors.errors && validationErrors.errors.length > 0 && (
                                    <ul className="list-disc list-inside space-y-1 text-sm">
                                        {validationErrors.errors.map((error, idx) => (
                                            <li key={idx}>
                                                <strong>{error.field}:</strong> {error.message}
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                {validationErrors.documentation_url && (
                                    <a
                                        href={validationErrors.documentation_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 text-sm underline mt-2"
                                    >
                                        View documentation <ExternalLink className="w-3 h-3" />
                                    </a>
                                )}
                            </AlertDescription>
                        </Alert>
                    )}

                    {/* Schema-driven Fields */}
                    {providerSchema && providerSchema.fields && (
                        <>
                            {/* Authentication Type Info */}
                            {providerSchema.auth_type === 'api_key_or_aws_iam' && (
                                <Alert>
                                    <Info className="h-4 w-4" />
                                    <AlertDescription className="text-sm">
                                        This provider supports multiple authentication methods. 
                                        Provide either an API key OR AWS IAM credentials (Access Key ID + Secret Access Key).
                                    </AlertDescription>
                                </Alert>
                            )}

                            {/* Render fields from schema */}
                            {providerSchema.fields.map(field => renderFieldInput(field))}

                            {/* Documentation Link */}
                            {providerSchema.documentation_url && (
                                <div className="rounded-md border bg-muted/50 p-3 text-sm">
                                    <div className="flex items-center gap-2 mb-1">
                                        <Info className="w-4 h-4" />
                                        <span className="font-medium">Need help?</span>
                                    </div>
                                    <a
                                        href={providerSchema.documentation_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-700 underline"
                                    >
                                        View {providerSchema.display_name} documentation
                                        <ExternalLink className="w-3 h-3" />
                                    </a>
                                </div>
                            )}
                        </>
                    )}

                    {/* Security Notice */}
                    <div className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">
                        <div className="flex items-center gap-2 mb-1">
                            <Shield className="w-4 h-4" />
                            <span className="font-medium">Secure Storage</span>
                        </div>
                        Credentials are stored securely in the database and masked when retrieved.
                    </div>
                </div>

                <DialogFooter className="mt-6">
                    <Button variant="outline" onClick={onClose} disabled={loading}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={loading || !selectedProvider}>
                        {loading ? 'Saving...' : editingProvider ? 'Update' : 'Save'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default ModelKeyDialog;
