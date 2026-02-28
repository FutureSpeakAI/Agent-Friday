/**
 * IPC handlers for the Agent Network Protocol (Track VII, Phase 2).
 *
 * All handlers are prefixed with 'agent-net:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  agentNetwork,
  type AgentIdentity,
  type AgentMessage,
  type AgentNetworkConfig,
} from '../agent-network';

export function registerAgentNetworkHandlers(): void {
  // ── Identity ─────────────────────────────────────────────────────────

  ipcMain.handle('agent-net:get-identity', () => {
    return agentNetwork.getIdentity();
  });

  ipcMain.handle('agent-net:get-agent-id', () => {
    return agentNetwork.getAgentId();
  });

  // ── Pairing ──────────────────────────────────────────────────────────

  ipcMain.handle('agent-net:generate-pairing-offer', () => {
    return agentNetwork.generatePairingOffer();
  });

  ipcMain.handle('agent-net:get-active-pairing-code', () => {
    return agentNetwork.getActivePairingCode();
  });

  ipcMain.handle(
    'agent-net:accept-pairing',
    (_event, remoteIdentity: AgentIdentity, ownerPersonId: string | null, ownerTrust: { overall: number } | null) => {
      return agentNetwork.acceptPairing(remoteIdentity, ownerPersonId, ownerTrust);
    }
  );

  ipcMain.handle(
    'agent-net:record-inbound-pairing',
    (_event, remoteIdentity: AgentIdentity) => {
      return agentNetwork.recordInboundPairingRequest(remoteIdentity);
    }
  );

  ipcMain.handle('agent-net:block-agent', (_event, agentId: string) => {
    return agentNetwork.blockAgent(agentId);
  });

  ipcMain.handle('agent-net:unpair-agent', (_event, agentId: string) => {
    return agentNetwork.unpairAgent(agentId);
  });

  // ── Peers ────────────────────────────────────────────────────────────

  ipcMain.handle('agent-net:get-peer', (_event, agentId: string) => {
    return agentNetwork.getPeer(agentId);
  });

  ipcMain.handle('agent-net:get-all-peers', () => {
    return agentNetwork.getAllPeers();
  });

  ipcMain.handle('agent-net:get-paired-peers', () => {
    return agentNetwork.getPairedPeers();
  });

  ipcMain.handle('agent-net:get-pending-pairing-requests', () => {
    return agentNetwork.getPendingPairingRequests();
  });

  // ── Trust ────────────────────────────────────────────────────────────

  ipcMain.handle(
    'agent-net:update-peer-trust',
    (_event, agentId: string, ownerTrust: { overall: number } | null, ownerPersonId?: string) => {
      return agentNetwork.updatePeerTrust(agentId, ownerTrust, ownerPersonId);
    }
  );

  ipcMain.handle(
    'agent-net:set-auto-approve-task-types',
    (_event, agentId: string, taskTypes: string[]) => {
      return agentNetwork.setAutoApproveTaskTypes(agentId, taskTypes);
    }
  );

  // ── Capabilities ─────────────────────────────────────────────────────

  ipcMain.handle(
    'agent-net:update-peer-capabilities',
    (_event, agentId: string, capabilities: string[]) => {
      return agentNetwork.updatePeerCapabilities(agentId, capabilities);
    }
  );

  ipcMain.handle(
    'agent-net:find-peers-with-capability',
    (_event, capability: string) => {
      return agentNetwork.findPeersWithCapability(capability);
    }
  );

  // ── Messaging ────────────────────────────────────────────────────────

  ipcMain.handle(
    'agent-net:create-message',
    (_event, toAgentId: string, type: string, payload: Record<string, unknown>) => {
      return agentNetwork.createMessage(toAgentId, type as any, payload);
    }
  );

  ipcMain.handle(
    'agent-net:process-inbound-message',
    (_event, message: AgentMessage) => {
      return agentNetwork.processInboundMessage(message);
    }
  );

  ipcMain.handle('agent-net:get-message-log', (_event, limit?: number) => {
    return agentNetwork.getMessageLog(limit);
  });

  // ── Task Delegation ──────────────────────────────────────────────────

  ipcMain.handle(
    'agent-net:create-delegation',
    (_event, targetAgentId: string, description: string, requiredCapabilities?: string[], deadline?: number) => {
      return agentNetwork.createDelegation(targetAgentId, description, requiredCapabilities, deadline);
    }
  );

  ipcMain.handle(
    'agent-net:handle-inbound-delegation',
    (_event, requestingAgentId: string, delegationId: string, description: string, requiredCapabilities: string[], deadline: number) => {
      return agentNetwork.handleInboundDelegation(requestingAgentId, delegationId, description, requiredCapabilities, deadline);
    }
  );

  ipcMain.handle('agent-net:approve-delegation', (_event, delegationId: string) => {
    return agentNetwork.approveDelegation(delegationId);
  });

  ipcMain.handle('agent-net:reject-delegation', (_event, delegationId: string) => {
    return agentNetwork.rejectDelegation(delegationId);
  });

  ipcMain.handle('agent-net:start-delegation', (_event, delegationId: string) => {
    return agentNetwork.startDelegation(delegationId);
  });

  ipcMain.handle(
    'agent-net:complete-delegation',
    (_event, delegationId: string, result: unknown) => {
      return agentNetwork.completeDelegation(delegationId, result);
    }
  );

  ipcMain.handle(
    'agent-net:fail-delegation',
    (_event, delegationId: string, error: string) => {
      return agentNetwork.failDelegation(delegationId, error);
    }
  );

  ipcMain.handle('agent-net:cancel-delegation', (_event, delegationId: string) => {
    return agentNetwork.cancelDelegation(delegationId);
  });

  ipcMain.handle('agent-net:get-delegation', (_event, delegationId: string) => {
    return agentNetwork.getDelegation(delegationId);
  });

  ipcMain.handle('agent-net:get-all-delegations', () => {
    return agentNetwork.getAllDelegations();
  });

  ipcMain.handle(
    'agent-net:get-delegations-for-agent',
    (_event, agentId: string) => {
      return agentNetwork.getDelegationsForAgent(agentId);
    }
  );

  ipcMain.handle('agent-net:get-pending-inbound-delegations', () => {
    return agentNetwork.getPendingInboundDelegations();
  });

  // ── Stats & Config ───────────────────────────────────────────────────

  ipcMain.handle('agent-net:get-stats', () => {
    return agentNetwork.getStats();
  });

  ipcMain.handle('agent-net:get-config', () => {
    return agentNetwork.getConfig();
  });

  ipcMain.handle(
    'agent-net:update-config',
    (_event, partial: Partial<AgentNetworkConfig>) => {
      return agentNetwork.updateConfig(partial);
    }
  );

  ipcMain.handle('agent-net:get-prompt-context', () => {
    return agentNetwork.getPromptContext();
  });
}
