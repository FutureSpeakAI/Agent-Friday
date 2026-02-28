/**
 * IPC handlers for the Superpower Ecosystem (Track VII, Phase 3).
 *
 * All handlers are prefixed with 'ecosystem:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  superpowerEcosystem,
  type SuperpowerManifest,
  type SignedPackage,
  type RegistrySearchQuery,
  type EcosystemConfig,
  type ManifestPricing,
  type ManifestCapability,
  type SuperpowerPermission,
  type ManifestEntry,
  type ManifestSandbox,
  type SuperpowerCategory,
  type ManifestAuthor,
  type ManifestDependency,
  type FinancialTransaction,
} from '../superpower-ecosystem';

export function registerSuperpowerEcosystemHandlers(): void {
  // ── Manifest ───────────────────────────────────────────────────────

  ipcMain.handle(
    'ecosystem:create-manifest',
    (_event, opts: {
      packageId: string;
      name: string;
      description: string;
      tagline: string;
      version: string;
      author: ManifestAuthor;
      license: string;
      repository: string;
      capabilities: ManifestCapability[];
      permissions: SuperpowerPermission[];
      dependencies?: ManifestDependency[];
      entry: ManifestEntry;
      sandbox: ManifestSandbox;
      category: SuperpowerCategory;
      tags?: string[];
      pricing?: Partial<ManifestPricing>;
      platforms?: ('win32' | 'darwin' | 'linux')[];
      homepage?: string;
      minAgentVersion?: string;
    }) => {
      return superpowerEcosystem.createManifest(opts);
    },
  );

  ipcMain.handle(
    'ecosystem:validate-manifest',
    (_event, manifest: SuperpowerManifest) => {
      return superpowerEcosystem.validateManifest(manifest);
    },
  );

  // ── Developer Tools ────────────────────────────────────────────────

  ipcMain.handle('ecosystem:get-developer-keys', () => {
    return superpowerEcosystem.getDeveloperKeys();
  });

  ipcMain.handle('ecosystem:has-developer-keys', () => {
    return superpowerEcosystem.hasDeveloperKeys();
  });

  ipcMain.handle(
    'ecosystem:sign-package',
    (_event, manifest: SuperpowerManifest) => {
      return superpowerEcosystem.signPackage(manifest);
    },
  );

  // ── Publishing ─────────────────────────────────────────────────────

  ipcMain.handle(
    'ecosystem:publish-package',
    (_event, pkg: SignedPackage) => {
      return superpowerEcosystem.publishPackage(pkg);
    },
  );

  ipcMain.handle('ecosystem:get-published-packages', () => {
    return superpowerEcosystem.getPublishedPackages();
  });

  ipcMain.handle(
    'ecosystem:get-published-package',
    (_event, packageId: string) => {
      return superpowerEcosystem.getPublishedPackage(packageId);
    },
  );

  ipcMain.handle(
    'ecosystem:unpublish-package',
    (_event, packageId: string) => {
      return superpowerEcosystem.unpublishPackage(packageId);
    },
  );

  // ── Registry ───────────────────────────────────────────────────────

  ipcMain.handle(
    'ecosystem:search-registry',
    (_event, query: RegistrySearchQuery) => {
      return superpowerEcosystem.searchRegistry(query);
    },
  );

  ipcMain.handle(
    'ecosystem:get-registry-listing',
    (_event, packageId: string) => {
      return superpowerEcosystem.getRegistryListing(packageId);
    },
  );

  ipcMain.handle(
    'ecosystem:search-for-capability',
    (_event, description: string, keywords: string[]) => {
      return superpowerEcosystem.searchForCapability(description, keywords);
    },
  );

  // ── Purchases ──────────────────────────────────────────────────────

  ipcMain.handle(
    'ecosystem:initiate-purchase',
    (_event, packageId: string, amountUsdCents: number, type?: FinancialTransaction['type']) => {
      return superpowerEcosystem.initiatePurchase(packageId, amountUsdCents, type);
    },
  );

  ipcMain.handle(
    'ecosystem:approve-purchase',
    (_event, transactionId: string, consentToken: string) => {
      return superpowerEcosystem.approvePurchase(transactionId, consentToken);
    },
  );

  ipcMain.handle(
    'ecosystem:cancel-purchase',
    (_event, transactionId: string) => {
      return superpowerEcosystem.cancelPurchase(transactionId);
    },
  );

  ipcMain.handle(
    'ecosystem:execute-purchase',
    (_event, transactionId: string) => {
      return superpowerEcosystem.executePurchase(transactionId);
    },
  );

  ipcMain.handle('ecosystem:get-transactions', () => {
    return superpowerEcosystem.getTransactions();
  });

  ipcMain.handle(
    'ecosystem:get-transactions-for-package',
    (_event, packageId: string) => {
      return superpowerEcosystem.getTransactionsForPackage(packageId);
    },
  );

  ipcMain.handle(
    'ecosystem:get-transaction',
    (_event, transactionId: string) => {
      return superpowerEcosystem.getTransaction(transactionId);
    },
  );

  ipcMain.handle(
    'ecosystem:is-purchased',
    (_event, packageId: string) => {
      return superpowerEcosystem.isPurchased(packageId);
    },
  );

  // ── Stats & Config ─────────────────────────────────────────────────

  ipcMain.handle('ecosystem:get-stats', () => {
    return superpowerEcosystem.getStats();
  });

  ipcMain.handle('ecosystem:get-config', () => {
    return superpowerEcosystem.getConfig();
  });

  ipcMain.handle(
    'ecosystem:update-config',
    (_event, partial: Partial<EcosystemConfig>) => {
      return superpowerEcosystem.updateConfig(partial);
    },
  );

  ipcMain.handle('ecosystem:get-prompt-context', () => {
    return superpowerEcosystem.getPromptContext();
  });
}
