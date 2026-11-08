# Signing keys

`index.json` is signed with [minisign](https://jedisct1.github.io/minisign/) (Ed25519). The public keys are here;
the secret key is kept outside every repository and reaches CI only as the `MINISIGN_SECRET_KEY` secret.

| File | Key id |
|---|---|
| `openig2-mods.pub` | `A9B771443098FFEF` |

Check an index by hand:

```sh
minisign -Vm index.json -p keys/openig2-mods.pub
```

**Rotation.** A second public key may be added here. The openIG2 client builds in both and accepts an index signed by
either, so a key is replaced in this order: add the new `.pub` here, ship an openIG2 release that has it, switch the
secret to the new key, and only then remove the old `.pub` (and drop it from the client in a later release).
