"""Steamworks transport backed by the installed game DLL; no credentials stored."""
from pathlib import Path
from contextlib import contextmanager
from functools import wraps
import ctypes as c
from datetime import datetime, timezone
import json
import os
import struct
import time
import sys
import urllib.parse
import urllib.request
from .discovery import find_game
from .filesystem import file_hash, inventory,save_record
APP_ID = 1669000


@contextmanager
def native_output_to_stderr():
    """Keep SDK startup/shutdown CRT and Win32 writes out of CLI JSON stdout."""
    sys.stdout.flush();sys.stderr.flush()
    saved=os.dup(1);kernel=None;previous=None
    try:
        if os.name=='nt':
            kernel=c.WinDLL('kernel32',use_last_error=True)
            kernel.GetStdHandle.argtypes=[c.c_int32];kernel.GetStdHandle.restype=c.c_void_p
            kernel.SetStdHandle.argtypes=[c.c_int32,c.c_void_p];kernel.SetStdHandle.restype=c.c_bool
            previous=kernel.GetStdHandle(-11);error=kernel.GetStdHandle(-12)
            if error in (None,c.c_void_p(-1).value) or not kernel.SetStdHandle(-11,error):
                raise RuntimeError('Cannot redirect native Steam diagnostics')
        os.dup2(2,1)
        yield
    finally:
        try:
            if kernel:
                runtime=c.CDLL('ucrtbase');runtime.fflush.argtypes=[c.c_void_p];runtime.fflush.restype=c.c_int
                runtime.fflush(None)
            sys.stdout.flush();sys.stderr.flush()
        finally:
            os.dup2(saved,1);os.close(saved)
            if kernel and previous is not None:kernel.SetStdHandle(-11,previous)


def native_diagnostics(function):
    @wraps(function)
    def call(*args,**kwargs):
        with native_output_to_stderr():return function(*args,**kwargs)
    return call

def public_details(item_id, proxy):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}) if proxy else urllib.request.ProxyHandler())
    body = urllib.parse.urlencode({"itemcount": 1, "publishedfileids[0]": str(item_id)}).encode()
    request = urllib.request.Request("https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/", data=body)
    with opener.open(request, timeout=30) as response:
        result = json.load(response)["response"]["publishedfiledetails"][0]
    if result["result"] != 1:
        raise RuntimeError(f"Workshop item unavailable: {result['result']}")
    return result



class CallbackMsg(c.Structure):
    _fields_ = [("user", c.c_int), ("callback", c.c_int),
                ("parameter", c.c_void_p), ("size", c.c_int)]


class UGCDetailsPrefix(c.Structure):
    _pack_=8
    _fields_=[('item',c.c_uint64),('result',c.c_int32),('file_type',c.c_int32),
              ('creator_app',c.c_uint32),('consumer_app',c.c_uint32),
              ('title',c.c_char*129),('description',c.c_char*8000),('owner',c.c_uint64),
              ('created',c.c_uint32),('updated',c.c_uint32),('added',c.c_uint32),('visibility',c.c_int32),
              ('banned',c.c_bool),('accepted',c.c_bool),('tags_truncated',c.c_bool),('tags',c.c_char*1025),
              ('file_handle',c.c_uint64),('preview_handle',c.c_uint64)]


class StringArray(c.Structure):
    _fields_=[('strings',c.POINTER(c.c_char_p)),('count',c.c_int)]


class Steam:
    @native_diagnostics
    def __init__(self,game=None):
        if os.name!='nt' or c.sizeof(c.c_void_p)!=8:
            raise RuntimeError('Steam publishing transport currently supports Windows 64-bit only')
        self.game=find_game(game)
        self.previous_env={key:os.environ.get(key) for key in ('SteamAppId','SteamGameId')}
        self.manual_dispatch=False
        self.initialized=False;self.closed=False;self.search_path=None
        try:
            os.environ["SteamAppId"] = str(APP_ID)
            os.environ["SteamGameId"] = str(APP_ID)
            self.search_path = os.add_dll_directory(str(self.game))
            self.dll = c.CDLL(str(self.game / "steam_api64.dll"))
            if not self.fn("SteamAPI_Init", [], c.c_bool)():
                raise RuntimeError("Steam initialization failed; Steam must be running and signed in")
            self.initialized=True;self.interface_versions={}
            self.user = self.interface('SteamAPI_SteamUser_',('v023','v022','v021'))
            self.utils = self.interface('SteamAPI_SteamUtils_',('v010',))
            self.ugc = self.interface('SteamAPI_SteamUGC_',('v021','v020','v019','v018','v017','v016'))
            if not all((self.user, self.utils, self.ugc)):
                raise RuntimeError("Required Steam interface is unavailable")
            app = self.fn("SteamAPI_ISteamUtils_GetAppID", [c.c_void_p], c.c_uint32)(self.utils)
            if app != APP_ID:raise RuntimeError("Unexpected Steam app context")
        except BaseException:
            self.close()
            raise

    def fn(self, name, args, result):
        try:f = getattr(self.dll, name)
        except AttributeError as exc:raise RuntimeError('Steam SDK export unavailable: '+name) from exc
        f.argtypes, f.restype = args, result
        return f

    def interface(self,prefix,versions):
        for version in versions:
            if hasattr(self.dll,prefix+version):
                value=self.fn(prefix+version,[],c.c_void_p)()
                if value:
                    self.interface_versions[prefix]=version
                    return value
        return None

    @native_diagnostics
    def close(self):
        if self.closed:return
        self.closed=True
        try:
            if self.initialized:self.fn("SteamAPI_Shutdown", [], None)()
        finally:
            if self.search_path:self.search_path.close()
            for key,value in self.previous_env.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value

    def steam_id(self):
        return self.fn("SteamAPI_ISteamUser_GetSteamID", [c.c_void_p], c.c_uint64)(self.user)

    def dlc_status(self,app_ids):
        """Read-only client eligibility check; never installs or activates DLC."""
        apps=self.interface('SteamAPI_SteamApps_',('v009','v008'))
        if not apps:raise RuntimeError('Steam Apps interface unavailable')
        installed=self.fn('SteamAPI_ISteamApps_BIsDlcInstalled',[c.c_void_p,c.c_uint32],c.c_bool)
        return {str(app_id):bool(installed(apps,int(app_id))) for app_id in sorted(set(app_ids))}

    def _dispatch(self):
        if not self.manual_dispatch:
            self.fn('SteamAPI_ManualDispatch_Init',[],None)();self.manual_dispatch=True
        return self.fn('SteamAPI_GetHSteamPipe',[],c.c_int)()

    def _wait_call(self,call,callback_id,min_size,max_size,timeout):
        if not call:raise RuntimeError('Steam returned an invalid API call handle')
        pipe=self._dispatch();deadline=time.monotonic()+timeout
        run=self.fn('SteamAPI_ManualDispatch_RunFrame',[c.c_int],None)
        get=self.fn('SteamAPI_ManualDispatch_GetNextCallback',[c.c_int,c.POINTER(CallbackMsg)],c.c_bool)
        free=self.fn('SteamAPI_ManualDispatch_FreeLastCallback',[c.c_int],None)
        result=self.fn('SteamAPI_ManualDispatch_GetAPICallResult',[c.c_int,c.c_uint64,c.c_void_p,c.c_int,c.c_int,c.POINTER(c.c_bool)],c.c_bool)
        while time.monotonic()<deadline:
            run(pipe);message=CallbackMsg()
            while get(pipe,c.byref(message)):
                try:
                    if message.callback!=703 or message.size!=16:continue
                    completed,kind,size=struct.unpack('<QII',c.string_at(message.parameter,message.size))
                    if completed!=call:continue
                    if kind!=callback_id or not min_size<=size<=max_size:raise RuntimeError('Unsupported Steam callback layout')
                    buffer=c.create_string_buffer(size);failed=c.c_bool()
                    if not result(pipe,call,buffer,size,kind,c.byref(failed)):raise RuntimeError('Steam callback result is unavailable')
                    return buffer.raw,bool(failed.value)
                finally:free(pipe)
            time.sleep(0.1)
        raise TimeoutError('Steam API result was not observed before timeout; outcome is unknown')

    def _query(self,handle,timeout):
        if handle in (0,0xffffffffffffffff):raise RuntimeError('Steam rejected the UGC query')
        release=self.fn('SteamAPI_ISteamUGC_ReleaseQueryUGCRequest',[c.c_void_p,c.c_uint64],c.c_bool)
        try:
            self._dispatch()
            if not self.fn('SteamAPI_ISteamUGC_SetReturnLongDescription',[c.c_void_p,c.c_uint64,c.c_bool],c.c_bool)(self.ugc,handle,True):
                raise RuntimeError('Steam rejected full-description query mode')
            if not self.fn('SteamAPI_ISteamUGC_SetAllowCachedResponse',[c.c_void_p,c.c_uint64,c.c_uint32],c.c_bool)(self.ugc,handle,0):
                raise RuntimeError('Steam rejected fresh query mode')
            call=self.fn('SteamAPI_ISteamUGC_SendQueryUGCRequest',[c.c_void_p,c.c_uint64],c.c_uint64)(self.ugc,handle)
            raw,failed=self._wait_call(call,3401,21,1024,timeout)
            actual,result,count,total=struct.unpack_from('<QiII',raw)
            if failed or result!=1 or actual!=handle or count>1000:raise RuntimeError('UGC query failed or returned an unexpected result')
            items=[];read=self.fn('SteamAPI_ISteamUGC_GetQueryUGCResult',[c.c_void_p,c.c_uint64,c.c_uint32,c.c_void_p],c.c_bool)
            for i in range(count):
                # The current SDK writes its full details struct; parse only its documented stable prefix.
                buffer=c.create_string_buffer(65536)
                if not read(self.ugc,handle,i,buffer):raise RuntimeError('Cannot read queried UGC details')
                item=UGCDetailsPrefix.from_buffer_copy(buffer.raw[:c.sizeof(UGCDetailsPrefix)])
                if item.result!=1 or not item.item or not item.owner or item.consumer_app!=APP_ID:
                    raise RuntimeError('UGC details identity/layout check failed')
                items.append({'publishedfileid':str(item.item),'consumer_app_id':item.consumer_app,'creator':str(item.owner),
                    'title':bytes(item.title).decode('utf8'),'description':bytes(item.description).decode('utf8'),
                    'time_created':item.created,'time_updated':item.updated,'visibility':item.visibility,
                    'tags':[x for x in bytes(item.tags).decode('utf8').split(',') if x],
                    'tags_truncated':bool(item.tags_truncated),'banned':bool(item.banned),
                    'preview_present':item.preview_handle not in (0,0xffffffffffffffff)})
            return {'items':items,'total':total}
        finally:release(self.ugc,handle)

    def item_details(self,item_id,timeout=30):
        ids=(c.c_uint64*1)(item_id)
        handle=self.fn('SteamAPI_ISteamUGC_CreateQueryUGCDetailsRequest',[c.c_void_p,c.POINTER(c.c_uint64),c.c_uint32],c.c_uint64)(self.ugc,ids,1)
        result=self._query(handle,timeout)
        if len(result['items'])!=1 or int(result['items'][0]['publishedfileid'])!=item_id:raise RuntimeError('Requested Workshop item was not returned')
        return result['items'][0]

    def owned_items(self,page=1,timeout=30):
        if not 1<=page<=10000:raise RuntimeError('Invalid Workshop page')
        owner=self.steam_id()
        handle=self.fn('SteamAPI_ISteamUGC_CreateQueryUserUGCRequest',
            [c.c_void_p,c.c_uint32,c.c_int,c.c_int,c.c_int,c.c_uint32,c.c_uint32,c.c_uint32],c.c_uint64)(
                self.ugc,owner&0xffffffff,0,0,0,APP_ID,APP_ID,page)
        result=self._query(handle,timeout)
        if any(int(item['creator'])!=owner for item in result['items']):raise RuntimeError('Owned-item query returned a different author')
        return {**result,'page':page,'owner':str(owner),'next_page':page+1 if page*50<result['total'] else None}

    def create_item(self,context,receipt_path,timeout=90):
        required=('CreateItem','SetItemTitle','SetItemTags','SetItemVisibility','SetItemPreview',
                  'StartItemUpdate','SubmitItemUpdate','CreateQueryUGCDetailsRequest','GetQueryUGCResult')
        if hasattr(self,'dll'):
            missing=[name for name in required if not hasattr(self.dll,'SteamAPI_ISteamUGC_'+name)]
            if missing:raise RuntimeError('First-publication SDK capabilities missing: '+', '.join(missing))
        create=self.fn('SteamAPI_ISteamUGC_CreateItem',[c.c_void_p,c.c_uint32,c.c_int],c.c_uint64)
        self._dispatch()
        receipt={'format':'aow4mod-creation-receipt/1','app_id':APP_ID,'status':'calling_create',
                 'created_utc':datetime.now(timezone.utc).isoformat(),**context}
        with Path(receipt_path).open('x',encoding='utf8') as f:
            json.dump(receipt,f,indent=2);f.flush();os.fsync(f.fileno())
        try:
            call=create(self.ugc,APP_ID,0);receipt['api_call']=call
            save_record(receipt_path,receipt)
            raw,failed=self._wait_call(call,3403,17,64,timeout)
            result=struct.unpack_from('<i',raw)[0];item=struct.unpack_from('<Q',raw,8)[0];legal=bool(raw[16])
            receipt.update(steam_result=result,returned_item_id=item,io_failure=failed,needs_workshop_agreement=legal,
                           status='created' if result==1 and item and not failed else 'creation_failed',
                           completed_utc=datetime.now(timezone.utc).isoformat())
            if receipt['status']=='created':receipt['published_file_id']=item
            save_record(receipt_path,receipt)
            if receipt['status']!='created':raise RuntimeError('Steam did not confirm item creation; inspect the receipt')
            return receipt
        except BaseException as exc:
            if receipt['status']=='calling_create':receipt['status']='creation_outcome_unknown'
            receipt['error']=str(exc)
            save_record(receipt_path,receipt)
            raise

    def publish(self, manifest, content, receipt_path, timeout):
        pipe = self._dispatch()
        start = self.fn("SteamAPI_ISteamUGC_StartItemUpdate", [c.c_void_p, c.c_uint32, c.c_uint64], c.c_uint64)
        handle = start(self.ugc, APP_ID, manifest["published_file_id"])
        if handle in (0, 0xFFFFFFFFFFFFFFFF):
            raise RuntimeError("StartItemUpdate failed")
        description = Path(manifest["description_file"]).read_text(encoding="utf-8").rstrip()
        notes = Path(manifest["change_notes_file"]).read_text(encoding="utf-8").rstrip()
        for name, value in (("SetItemContent", str(content)), ("SetItemDescription", description)):
            setter = self.fn("SteamAPI_ISteamUGC_" + name, [c.c_void_p, c.c_uint64, c.c_char_p], c.c_bool)
            if not setter(self.ugc, handle, value.encode("utf-8")):
                raise RuntimeError(name + " rejected the prepared update")
        initial=manifest.get('_new_item_metadata')
        if initial:
            if file_hash(initial['preview'])!=initial['preview_sha256']:raise RuntimeError('Prepared preview changed')
            for name,value in (('SetItemTitle',initial['title']),('SetItemPreview',initial['preview'])):
                if not self.fn('SteamAPI_ISteamUGC_'+name,[c.c_void_p,c.c_uint64,c.c_char_p],c.c_bool)(self.ugc,handle,value.encode('utf8')):
                    raise RuntimeError(name+' rejected first-publication metadata')
            if not self.fn('SteamAPI_ISteamUGC_SetItemVisibility',[c.c_void_p,c.c_uint64,c.c_int],c.c_bool)(self.ugc,handle,initial['visibility']):
                raise RuntimeError('Steam rejected initial visibility')
            strings=(c.c_char_p*len(initial['tags']))(*(t.encode('utf8') for t in initial['tags']))
            array=StringArray(strings,len(strings))
            if not self.fn('SteamAPI_ISteamUGC_SetItemTags',[c.c_void_p,c.c_uint64,c.POINTER(StringArray)],c.c_bool)(self.ugc,handle,c.byref(array)):
                raise RuntimeError('Steam rejected initial tags')
        # Existing title, preview, tags, visibility and arbitrary Steam metadata
        # are preserved. The new version lives in .metadata/metadata.json.
        receipt = {"app_id": APP_ID, "published_file_id": manifest["published_file_id"],
                   "started_utc": datetime.now(timezone.utc).isoformat(),
                   "status": "submitting", "content_files": manifest["files"],
                   "manifest_sha256":manifest.get('_manifest_sha256'),
                   "review_override":manifest.get('_review_override',False),
                   "staging_directory":manifest.get('_staging_directory')}
        with receipt_path.open("x", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2);f.flush();os.fsync(f.fileno())
        submit = self.fn("SteamAPI_ISteamUGC_SubmitItemUpdate", [c.c_void_p, c.c_uint64, c.c_char_p], c.c_uint64)
        call = submit(self.ugc, handle, notes.encode("utf-8"))
        if not call:
            receipt["status"] = "submit_failed"
            save_record(receipt_path,receipt)
            raise RuntimeError("SubmitItemUpdate returned an invalid call")
        receipt["api_call"] = call
        save_record(receipt_path,receipt)
        print(json.dumps({"status": "submitted", "item": manifest["published_file_id"]}), flush=True,file=sys.stderr)
        run = self.fn("SteamAPI_ManualDispatch_RunFrame", [c.c_int], None)
        next_callback = self.fn("SteamAPI_ManualDispatch_GetNextCallback", [c.c_int, c.POINTER(CallbackMsg)], c.c_bool)
        free = self.fn("SteamAPI_ManualDispatch_FreeLastCallback", [c.c_int], None)
        get_result = self.fn("SteamAPI_ManualDispatch_GetAPICallResult", [c.c_int, c.c_uint64, c.c_void_p, c.c_int, c.c_int, c.POINTER(c.c_bool)], c.c_bool)
        progress = self.fn("SteamAPI_ISteamUGC_GetItemUpdateProgress", [c.c_void_p, c.c_uint64, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64)], c.c_int)
        deadline, last_progress, last_print = time.monotonic() + timeout, None, 0
        while time.monotonic() < deadline:
            run(pipe)
            message = CallbackMsg()
            while next_callback(pipe, c.byref(message)):
                try:
                    if message.callback != 703 or message.size != 16:
                        continue
                    completed_call, callback_id, size = struct.unpack("<QII", c.string_at(message.parameter, message.size))
                    if completed_call != call:
                        continue
                    if callback_id != 3404 or not 5 <= size <= 64:
                        raise RuntimeError(f"Unexpected update-result layout: {callback_id}, {size}")
                    buffer, failed = c.create_string_buffer(size), c.c_bool()
                    if not get_result(pipe, call, buffer, size, callback_id, c.byref(failed)):
                        raise RuntimeError("Cannot read completed update result")
                    result = struct.unpack_from("<i", buffer.raw, 0)[0]
                    legal = bool(buffer.raw[4])
                    receipt.update(status="success" if result == 1 and not failed.value and not legal else "attention_required",
                                   steam_result=result, io_failure=failed.value, needs_workshop_agreement=legal,
                                   callback_size=size, completed_utc=datetime.now(timezone.utc).isoformat())
                    if size >= 16:
                        receipt["returned_item_id"] = struct.unpack_from("<Q", buffer.raw, 8)[0]
                        if receipt['returned_item_id']!=manifest['published_file_id']:
                            receipt['status']='result_identity_mismatch'
                    save_record(receipt_path,receipt)
                    print(json.dumps({k:receipt[k] for k in ('status','steam_result','published_file_id')}), flush=True,file=sys.stderr)
                    if receipt["status"] != "success":
                        raise RuntimeError("Steam did not confirm a fully completed update; inspect receipt")
                    return receipt
                finally:
                    free(pipe)
            done, total = c.c_uint64(), c.c_uint64()
            state = progress(self.ugc, handle, c.byref(done), c.byref(total))
            current = (state, done.value, total.value)
            if current != last_progress and time.monotonic() - last_print >= 2:
                print(json.dumps({"phase": state, "processed": done.value, "total": total.value}), flush=True,file=sys.stderr)
                last_progress, last_print = current, time.monotonic()
            time.sleep(0.2)
        receipt["status"] = "timeout_outcome_unknown"
        save_record(receipt_path,receipt)
        raise TimeoutError("Check Workshop remote state before retrying; no automatic resubmission")

    def verify_download(self,manifest,timeout=90):
        item=manifest['published_file_id']
        download=self.fn('SteamAPI_ISteamUGC_DownloadItem',[c.c_void_p,c.c_uint64,c.c_bool],c.c_bool)
        if not download(self.ugc,item,True):raise RuntimeError('Steam rejected verification download')
        info=self.fn('SteamAPI_ISteamUGC_GetItemInstallInfo',[c.c_void_p,c.c_uint64,c.POINTER(c.c_uint64),c.c_void_p,c.c_uint32,c.POINTER(c.c_uint32)],c.c_bool)
        deadline=time.monotonic()+timeout;last=None
        while time.monotonic()<deadline:
            if self.manual_dispatch:
                pipe=self.fn('SteamAPI_GetHSteamPipe',[],c.c_int)()
                self.fn('SteamAPI_ManualDispatch_RunFrame',[c.c_int],None)(pipe)
                message=CallbackMsg()
                get=self.fn('SteamAPI_ManualDispatch_GetNextCallback',[c.c_int,c.POINTER(CallbackMsg)],c.c_bool)
                free=self.fn('SteamAPI_ManualDispatch_FreeLastCallback',[c.c_int],None)
                while get(pipe,c.byref(message)):free(pipe)
            else:self.fn('SteamAPI_RunCallbacks',[],None)()
            size,stamp,folder=c.c_uint64(),c.c_uint32(),c.create_string_buffer(32768)
            if info(self.ugc,item,c.byref(size),folder,len(folder),c.byref(stamp)):
                root=Path(folder.value.decode('utf8'))
                try:
                    files=inventory(root)
                    hashes={k:v['sha256'] for k,v in files.items()}
                    if hashes==manifest['files']:
                        return {'content_matches':True,'installed_directory':str(root),'files':hashes,'install_timestamp':stamp.value}
                    last={'content_matches':False,'installed_directory':str(root),'observed_file_count':len(hashes)}
                except OSError:pass
            time.sleep(0.25)
        return last or {'content_matches':False,'reason':'download_not_verified_before_timeout'}
