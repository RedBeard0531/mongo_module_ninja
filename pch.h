#include "mongo/config.h"

// XXX this is needed for secure_zero_memory.cpp on OSX. Decide if we'd rather do this or opt-out of
// PCH for some files.
#if defined(MONGO_CONFIG_HAVE_MEMSET_S)
#define __STDC_WANT_LIB_EXT1__ 1
#endif

#include "mongo/platform/basic.h"

#include <memory>

#include "mongo/db/jsobj.h"
