import React, { createContext, useContext, useState, useEffect } from 'react';

const SchedulerContext = createContext();

export const useScheduler = () => {
  const context = useContext(SchedulerContext);
  if (!context) {
    throw new Error('useScheduler must be used within a SchedulerProvider');
  }
  return context;
};

export const SchedulerProvider = ({ children }) => {
  const [schedules, setSchedules] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  const isElectron = Boolean(typeof window !== 'undefined' && window?.electronAPI);

  // Helper: parse simple cron (m h * * *) into HH:MM
  const parseCronTime = (cron) => {
    if (!cron || typeof cron !== 'string') return undefined;
    const parts = cron.trim().split(/\s+/);
    if (parts.length < 2) return undefined;
    const [min, hour] = parts;
    if (isNaN(parseInt(hour)) || isNaN(parseInt(min))) return undefined;
    const pad = (n) => n.toString().padStart(2, '0');
    return `${pad(hour)}:${pad(min)}`;
  };

  // Transform from workflow file object to internal schedule shape
  const fromWorkflow = (wf) => {
    // Use a stable ID based on name hash to prevent regeneration on reload
    const stableId = wf.id || `${wf.name}-${wf.name.split('').reduce((a, b) => ((a << 5) - a) + b.charCodeAt(0), 0)}`;
    return {
      id: stableId,
      title: wf.name,
      name: wf.name,
      workflow: wf.workflow || wf.name,
      schedule: wf.schedule,
      enabled: wf.enabled ?? true,
      // Derive display time if cron fits pattern
      startTime: parseCronTime(wf.schedule),
      // Preserve date from saved data
      date: wf.date,
      createdAt: wf.createdAt || new Date().toISOString(),
      updatedAt: wf.updatedAt || new Date().toISOString()
    };
  };

  // Transform internal schedule back to workflow file object
  const toWorkflow = (sch) => {
    return {
      id: sch.id,
      name: sch.title || sch.name || 'untitled',
      workflow: sch.workflow || 'daily',
      date: sch.date,
      createdAt: sch.createdAt,
      updatedAt: sch.updatedAt,
      schedule: sch.schedule || (() => {
        // If we have startTime and type, synthesize cron
        if (sch.startTime) {
          const [hour, minute] = sch.startTime.split(':');
          const m = minute ?? '0';
          const h = hour ?? '0';
          if ((sch.workflow === 'weekly') && sch.date) {
            // Use day-of-week from date
            const dow = new Date(sch.date).getDay(); // 0-6
            return `${m} ${h} * * ${dow}`;
          }
          // Daily / default
          return `${m} ${h} * * *`;
        }
        return '*/5 * * * *'; // Fallback every 5 minutes
      })(),
      enabled: sch.enabled ?? true
    };
  };

  // Load schedules on mount
  useEffect(() => {
    const loadSchedules = async () => {
      try {
        console.log('Loading schedules, isElectron:', isElectron);
        setIsLoading(true);
        let savedSchedules = [];
        
        if (isElectron) {
          // Use Electron file operations
          try {
            console.log('Using Electron API to load schedules');
            savedSchedules = await window.electronAPI.loadSchedules();
            console.log('Loaded schedules from Electron:', savedSchedules);
          } catch (error) {
            console.error('Electron load failed:', error);
            savedSchedules = [];
          }
        } else {
          // Use localStorage for web
          console.log('Using localStorage to load schedules');
          const data = localStorage.getItem('oncall-schedules');
          savedSchedules = data ? JSON.parse(data) : [];
          console.log('Loaded schedules from localStorage:', savedSchedules);
        }
        
        const validSchedules = Array.isArray(savedSchedules) ? savedSchedules : [];
        // Detect workflow format: objects with name & schedule keys
        const normalized = validSchedules.map(obj => {
          if (obj && 'name' in obj && 'schedule' in obj && !('title' in obj)) {
            return fromWorkflow(obj);
          }
            // Already internal shape
          return obj;
        });
        console.log('Setting schedules (normalized):', normalized);
        setSchedules(normalized);
      } catch (error) {
        console.error('Error loading schedules:', error);
        setSchedules([]);
      } finally {
        setIsLoading(false);
      }
    };

    loadSchedules();
  }, [isElectron]);

  // Save schedules whenever schedules change
  useEffect(() => {
    if (isLoading) {
      return; // Don't save during initial load
    }
    
    const saveSchedules = async () => {
      try {
        console.log('saveSchedules called, isElectron:', isElectron, 'schedules count:', schedules.length);
        const workflowPayload = schedules.map(toWorkflow);
        if (isElectron) {
          // Use Electron file operations only; pass workflow format
          const result = await window.electronAPI.saveSchedules(workflowPayload);
          console.log('Save result:', result);
          if (!result.success) {
            console.error('Failed to save schedules to file:', result.error);
          }
        } else {
          // Use localStorage for web in workflow format for consistency
          localStorage.setItem('oncall-schedules', JSON.stringify(workflowPayload));
          console.log('Saved to localStorage');
        }
      } catch (error) {
        console.error('Error saving schedules:', error);
      }
    };

    saveSchedules();
  }, [schedules, isLoading, isElectron]);

  const addSchedule = (schedule) => {
    const newSchedule = {
      id: Date.now().toString(),
      ...schedule,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    setSchedules(prev => [...prev, newSchedule]);
  };

  const updateSchedule = (id, updatedSchedule) => {
    setSchedules(prev => prev.map(schedule => 
      schedule.id === id 
        ? { ...schedule, ...updatedSchedule, updatedAt: new Date().toISOString() }
        : schedule
    ));
  };

  const deleteSchedule = (id) => {
    console.log('deleteSchedule called with id:', id);
    console.log('Current schedules:', schedules);
    setSchedules(prev => {
      const filtered = prev.filter(schedule => schedule.id !== id);
      console.log('Schedules after delete:', filtered);
      return filtered;
    });
  };

  const getSchedule = (id) => {
    return schedules.find(schedule => schedule.id === id);
  };

  const getSchedulesByDate = (date) => {
    const targetDate = new Date(date).toDateString();
    return schedules.filter(schedule => 
      new Date(schedule.date).toDateString() === targetDate
    );
  };

  const value = {
    schedules,
    isLoading,
    addSchedule,
    updateSchedule,
    deleteSchedule,
    getSchedule,
    getSchedulesByDate
  };

  return (
    <SchedulerContext.Provider value={value}>
      {children}
    </SchedulerContext.Provider>
  );
};