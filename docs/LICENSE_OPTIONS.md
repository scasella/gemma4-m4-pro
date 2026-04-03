# License Options

This repo does not yet choose a license for you. The two most practical public-release options are:

## MIT

- simplest and most permissive
- easiest for outside reuse
- lowest friction if you mainly want people to learn from and adapt the toolkit

## Apache-2.0

- still permissive, but more explicit about patent-related terms
- a little heavier than MIT, but still common for engineering toolkits
- a reasonable default if you want a slightly more formal permissive license

## Before choosing

- keep model files out of the repo
- keep optional third-party checkouts out of the repo unless you are comfortable redistributing them in this shape
- make sure the final public repo only contains material you want others to copy and reuse

## Fast path

If you already know your choice, install a real root license file with:

```bash
./install_license.sh mit --holder "Your Name"
```

Or:

```bash
./install_license.sh apache-2.0 --holder "Your Name"
```
