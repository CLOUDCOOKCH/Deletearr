# Test through your NAS Docker interface

The `Publish NAS preview` workflow builds Linux AMD64 and ARM64 images at
`ghcr.io/cloudcookch/deletearr:preview`. Preview is a moving testing tag;
the workflow also publishes immutable `sha-<full-commit>` tags. AMD64 receives
a container startup/restart smoke test before publication. ARM64 builds do not
constitute verification on NAS hardware.

1. Open your NAS Docker application, create a Compose project, and paste
   [compose.nas.yaml](../compose.nas.yaml) into its YAML editor. Deploy it.
   No Git checkout, local build or SSH is needed. Keep port 8980 LAN-only.
2. Visit `http://YOUR-NAS-IP:8980`.
3. In the NAS Docker application, open the running container's **Terminal**
   or **Console** as the default container user, and run:

   ```sh
   python -c 'from pathlib import Path; print(Path("/config/setup-token").read_text())'
   ```

   This is the container console in the browser, not NAS SSH. Use the token
   to create your administrator; it disappears after successful setup.
   Do not post the token in support requests.
4. The initial Compose project deliberately has no media mounts. To index
   media, edit its volumes using the commented examples, select existing
   NAS folders, and redeploy without deleting the configuration volume.
   The application runs as UID/GID 10001; use the NAS file-permissions UI
   to grant it access. Never run as root to bypass permissions.
5. In Settings → Plex, choose **Sign in with Plex**. Authenticate on Plex's
   website; Deletarr will offer your owned servers. Choose a reachable
   connection, explicitly trust the LAN destination if applicable, and choose
   **Connect selected server**. Leave **Use this Plex account to sign in to
   Deletarr** checked to enable future Plex login. Your token stays on the
   backend. Local login remains available independently of Plex. Manual URL
   and token configuration is also available.
   Then
   select a test library, authorize the container media roots, and configure
   integration-to-container path mappings. Sync and simulate a rule.

New installations stay in dry-run mode. Do not activate permanent deletion
for initial testing. Use expendable media before testing any live operation.
Root authorization creates a mount sentinel and needs write access, even
when execution is dry-run. Optional manager integrations must also remain
under the application's dry-run protection.

If pulling reports `denied`, the package owner must open the GitHub package
settings and change its visibility to **Public**. GitHub can initially create
new container packages as private even when their source repository is public.
Do not enter a personal GitHub token into this application.

The named configuration volume survives container recreation. Do not remove
it when updating the image. It contains the database, encryption key and
administrator account. To update a preview, use your NAS UI's pull/recreate
action while retaining that volume. No automatic image updater is configured.
