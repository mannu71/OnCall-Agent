const API_BASE_URL = 'http://localhost:8000/api/v1';

export const getModelKeys = async () => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys`);
    if (!response.ok) throw new Error('Failed to fetch model keys');
    const data = await response.json();
    return data.keys || [];
  } catch (error) {
    console.error('Error fetching model keys:', error);
    return [];
  }
};

export const getModelKey = async (provider) => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys/${encodeURIComponent(provider)}`);
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to fetch model key');
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching model key:', error);
    throw error;
  }
};

export const createModelKey = async (data) => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create model key');
    }
    return await response.json();
  } catch (error) {
    console.error('Error creating model key:', error);
    throw error;
  }
};

export const updateModelKey = async (provider, data) => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys/${encodeURIComponent(provider)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update model key');
    }
    return await response.json();
  } catch (error) {
    console.error('Error updating model key:', error);
    throw error;
  }
};

export const upsertModelKey = async (data) => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys/upsert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to save model key');
    }
    return await response.json();
  } catch (error) {
    console.error('Error upserting model key:', error);
    throw error;
  }
};

export const deleteModelKey = async (provider) => {
  try {
    const response = await fetch(`${API_BASE_URL}/model-keys/${encodeURIComponent(provider)}`, {
      method: 'DELETE',
    });
    if (!response.ok && response.status !== 204) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete model key');
    }
    return true;
  } catch (error) {
    console.error('Error deleting model key:', error);
    throw error;
  }
};
