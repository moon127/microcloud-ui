import {
  backupExportUrl,
  createBackup,
  deleteBackup,
  fetchBackups,
  renameBackup,
  type InstanceFull,
} from "../../api";
import Backups from "../../Backups";

/** The instance's backups (IN-13, Phase 7a). */
export default function BackupsTab({ inst }: { inst: InstanceFull }) {
  const { project, name } = inst;
  return (
    <Backups
      project={project}
      name={name}
      only="instance_only"
      source={{
        key: `backups:${project}/${name}`,
        list: () => fetchBackups(project, name),
        create: (body) => createBackup(project, name, body),
        rename: (backup, to) => renameBackup(project, name, backup, to),
        remove: (backup) => deleteBackup(project, name, backup),
        exportUrl: (backup) => backupExportUrl(project, name, backup),
      }}
      intro={
        <>
          A backup is a tarball of the instance (and its snapshots, unless left out) that LXD keeps on the member running
          it, under that member&apos;s <code>storage.backups_volume</code>. Download it to keep a copy elsewhere;
          Instances → Import backup restores one as a new instance.
        </>
      }
    />
  );
}
