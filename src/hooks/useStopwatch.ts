import { useState, useEffect, useRef } from "react";

/**
 * Custom hook for a stopwatch timer
 * Returns elapsed time in seconds with 1 decimal precision
 */
export const useStopwatch = (isRunning: boolean) => {
  const [elapsed, setElapsed] = useState(0);
  const intervalRef = useRef<number | null>(null);
  const startTimeRef = useRef<number | null>(null);

  useEffect(() => {
    if (isRunning) {
      // Start the timer
      startTimeRef.current = Date.now();
      setElapsed(0);

      intervalRef.current = window.setInterval(() => {
        if (startTimeRef.current) {
          const now = Date.now();
          const diff = (now - startTimeRef.current) / 1000; // Convert to seconds
          setElapsed(diff);
        }
      }, 100); // Update every 100ms for smooth display
    } else {
      // Stop the timer
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      startTimeRef.current = null;
    }

    // Cleanup on unmount
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [isRunning]);

  const reset = () => {
    setElapsed(0);
    startTimeRef.current = null;
  };

  return { elapsed, reset };
};
