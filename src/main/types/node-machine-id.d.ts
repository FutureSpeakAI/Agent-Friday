declare module 'node-machine-id' {
  export function machineIdSync(options?: { original?: boolean }): string;
  export function machineId(options?: { original?: boolean }): Promise<string>;
}
