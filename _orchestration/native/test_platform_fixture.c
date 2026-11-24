#include "platform.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <wchar.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s (error %lu)\n",__LINE__,#x,(unsigned long)GetLastError()); exit(1); } } while (0)
static volatile LONG stop_flag;
static DWORD WINAPI cancel_thread(void *unused) { (void)unused; Sleep(120); InterlockedExchange(&stop_flag,1); return 0; }
static void notify(DWORD pid, void *context) { DWORD code; HANDLE h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid); CHECK(h); CHECK(GetExitCodeProcess(h,&code)); CHECK(code==STILL_ACTIVE); CloseHandle(h); *(DWORD*)context=pid; }
static char *u8(const wchar_t *s) { int n=WideCharToMultiByte(CP_UTF8,0,s,-1,NULL,0,NULL,NULL); char *v=malloc(n); CHECK(v); CHECK(WideCharToMultiByte(CP_UTF8,0,s,-1,v,n,NULL,NULL)); return v; }
static void print_start(DWORD pid,void *unused) { (void)unused;printf("root=%lu\n",(unsigned long)pid);fflush(stdout); }
static int child(int argc,wchar_t **argv) {
 if(!wcscmp(argv[1],L"supervise")) { CHECK(argc==4);char *exe=u8(argv[0]),*marker=u8(argv[2]),*log=u8(argv[3]);const char *a[]={exe,"tree",marker,NULL};return run_child_notify(a,NULL,log,10000,NULL,NULL,print_start,NULL); }
 if(!wcscmp(argv[1],L"argv")) {
  CHECK(argc==10); CHECK(!wcscmp(argv[2],L"")); CHECK(!wcscmp(argv[3],L"a b"));
  CHECK(!wcscmp(argv[4],L"a\"b")); CHECK(!wcscmp(argv[5],L"x\\"));
  CHECK(!wcscmp(argv[6],L"\\\"")); CHECK(!wcscmp(argv[7],L"中文路径")); CHECK(!wcscmp(argv[8],L"a\nb"));
  CHECK(!SetEvent((HANDLE)(uintptr_t)wcstoull(argv[9],NULL,10)));
  puts("argv-ok"); fputs("stderr-ok\n",stderr); return 7;
 }
 if(!wcscmp(argv[1],L"hold")) { FileLock l; char *p=u8(argv[2]); int ok=lock_file(&l,p,0); if(ok)unlock_file(&l); free(p); return ok?9:0; }
 if(!wcscmp(argv[1],L"sleep")) { Sleep(10000); return 0; }
 if(!wcscmp(argv[1],L"leaf")) { Sleep(2000); char *p=u8(argv[2]); CHECK(write_file(p,"BAD",3)); free(p); return 0; }
 if(!wcscmp(argv[1],L"tree")||!wcscmp(argv[1],L"tree-exit")) {
  wchar_t cmd[4096]; STARTUPINFOW si={0}; PROCESS_INFORMATION pi={0}; si.cb=sizeof(si);
  swprintf(cmd,4096,L"\"%ls\" leaf \"%ls\"",argv[0],argv[2]);
  CHECK(CreateProcessW(argv[0],cmd,NULL,NULL,FALSE,0,NULL,NULL,&si,&pi));
  printf("leaf=%lu\n",(unsigned long)pi.dwProcessId); fflush(stdout);
  CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
  if(!wcscmp(argv[1],L"tree")) Sleep(10000);
  return 0;
 }
 return 99;
}
static void dead_leaf(const char *log) {
 size_t n; char *data=read_file(log,&n); unsigned long id=0; CHECK(data); CHECK(sscanf(data,"leaf=%lu",&id)==1); free(data);
 HANDLE h=OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,(DWORD)id);
 if(h) { CHECK(WaitForSingleObject(h,0)==WAIT_OBJECT_0); CloseHandle(h); }
}
int wmain(int argc,wchar_t **argv) {
 if(argc>1) return child(argc,argv);
 char cwd[1024], file[4096], log[4096], marker[4096], p2[4096], backup[4096], lockpath[4096], deep[2048];
 wchar_t exe_w[4096]; CHECK(GetModuleFileNameW(NULL,exe_w,4096)); char *exe=u8(exe_w);
 wchar_t cwd_w[1024]; DWORD cwd_n=GetCurrentDirectoryW(1024,cwd_w); CHECK(cwd_n&&cwd_n<1024); CHECK(WideCharToMultiByte(CP_UTF8,0,cwd_w,-1,cwd,sizeof(cwd),NULL,NULL)); for(char *p=cwd;*p;++p)if(*p=='\\')*p='/';
 snprintf(file,sizeof(file),"%s/临时中文/a/../a/内容.bin",cwd);
 snprintf(deep,sizeof(deep),"%s/临时中文/a",cwd); CHECK(make_dirs(deep)); CHECK(dir_exists(deep)); CHECK(!file_exists(deep));
 CHECK(write_file(file,"abc",3)); CHECK(file_exists(file)); CHECK(!dir_exists(file));
 size_t n; char *data=read_file(file,&n); CHECK(data&&n==3&&!memcmp(data,"abc",3)&&!data[3]); free(data);
 char hash[65]; CHECK(sha256_file(file,hash)); CHECK(!strcmp(hash,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
 snprintf(backup,sizeof(backup),"%s/backup.bin",cwd); CHECK(copy_file(file,backup)); CHECK(!copy_file(file,backup)); CHECK(write_atomic(file,"changed",7));
 data=read_file(backup,&n); CHECK(data&&n==3&&!memcmp(data,"abc",3)); free(data);
 data=read_file(file,&n); CHECK(data&&n==7&&!memcmp(data,"changed",7)); free(data);
 char *abs=absolute_path(cwd,"临时中文/a/../a/内容.bin"); CHECK(abs&&!strchr(abs,'\\')); char *rel=root_relative(cwd,abs); CHECK(rel&&!strcmp(rel,"临时中文/a/内容.bin")); free(rel);
 snprintf(p2,sizeof(p2),"%s/does-not-exist/子目录/leaf",cwd); char *abs2=absolute_path(cwd,"does-not-exist/子目录/leaf"); if(!abs2||strcmp(abs2,p2))fprintf(stderr,"abs2=[%s] expected=[%s]\n",abs2?abs2:"NULL",p2); CHECK(abs2&&!strcmp(abs2,p2)); free(abs2);
 abs2=absolute_path("C:/","missing-abcd1234/child"); CHECK(abs2&&!strcmp(abs2,"C:/missing-abcd1234/child")); free(abs2);
 CHECK(!root_relative(deep,backup)); free(abs);
 char *snapshot=snapshot_tree(deep); CHECK(snapshot&&strstr(snapshot,"内容.bin")); CHECK(!strchr(snapshot,'\\')); CHECK(!strstr(snapshot,".tmp-"));
 char *again=snapshot_tree(deep); CHECK(again&&!strcmp(snapshot,again)); free(again); free(snapshot);
 snprintf(p2,sizeof(p2),"%s/empty",cwd); CHECK(make_dirs(p2)); snapshot=snapshot_tree(p2); CHECK(snapshot&&!strcmp(snapshot,"[]")); free(snapshot);
 HANDLE denied; wchar_t file_w[4096]; CHECK(MultiByteToWideChar(CP_UTF8,0,file,-1,file_w,4096)); denied=CreateFileW(file_w,GENERIC_WRITE,0,NULL,OPEN_EXISTING,0,NULL); CHECK(denied!=INVALID_HANDLE_VALUE); CHECK(!snapshot_tree(deep)); CloseHandle(denied);
 snprintf(log,sizeof(log),"%s/child.log",cwd); snprintf(lockpath,sizeof(lockpath),"%s/test.lock",cwd);
 FileLock l,l2; CHECK(lock_file(&l,lockpath,0)); CHECK(!lock_file(&l2,lockpath,0));
 const char *hold[]={exe,"hold",lockpath,NULL}; DWORD pid,notified=0; CHECK(run_child(hold,cwd,log,5000,&pid,NULL)==0); CHECK(pid); unlock_file(&l); CHECK(lock_file(&l2,lockpath,0)); unlock_file(&l2);
 SECURITY_ATTRIBUTES sa={sizeof(sa),NULL,TRUE}; HANDLE event=CreateEventW(&sa,TRUE,FALSE,NULL); CHECK(event); char handle_text[40]; snprintf(handle_text,sizeof(handle_text),"%llu",(unsigned long long)(uintptr_t)event);
 const char *args[]={exe,"argv","","a b","a\"b","x\\","\\\"","中文路径","a\nb",handle_text,NULL};
 CHECK(run_child_notify(args,cwd,log,5000,&pid,NULL,notify,&notified)==7); CHECK(pid==notified); CloseHandle(event);
 data=read_file(log,&n); CHECK(data&&strstr(data,"argv-ok")&&strstr(data,"stderr-ok")); free(data);
 const char *sleepy[]={exe,"sleep",NULL}; double t=monotonic_seconds(); CHECK(run_child(sleepy,cwd,log,150,&pid,NULL)==-2); CHECK(monotonic_seconds()-t<2.0);
 CHECK(run_child(sleepy,cwd,log,0,&pid,NULL)==-2);
 HANDLE thread=CreateThread(NULL,0,cancel_thread,NULL,0,NULL); CHECK(thread); CHECK(run_child(sleepy,cwd,log,5000,&pid,&stop_flag)==-3); CHECK(WaitForSingleObject(thread,1000)==WAIT_OBJECT_0); CloseHandle(thread);
 pid=123; CHECK(run_child(sleepy,cwd,log,5000,&pid,&stop_flag)==-3&&pid==0);
 const char *bad[]={"C:/does-not-exist/test.exe",NULL}; CHECK(run_child(bad,cwd,log,100,&pid,NULL)==-1);
 snprintf(marker,sizeof(marker),"%s/MUST_NOT_EXIST",cwd); const char *tree[]={exe,"tree",marker,NULL}; CHECK(run_child(tree,cwd,log,500,&pid,NULL)==-2); dead_leaf(log); CHECK(!file_exists(marker));
 const char *tree_exit[]={exe,"tree-exit",marker,NULL}; CHECK(run_child(tree_exit,cwd,log,5000,&pid,NULL)==0); dead_leaf(log); CHECK(!file_exists(marker));
 InterlockedExchange(&stop_flag,0); thread=CreateThread(NULL,0,cancel_thread,NULL,0,NULL); CHECK(thread); CHECK(run_child(tree,cwd,log,5000,&pid,&stop_flag)==-3); CHECK(WaitForSingleObject(thread,1000)==WAIT_OBJECT_0); CloseHandle(thread); dead_leaf(log); CHECK(!file_exists(marker));
 char *found=find_executable("cmd"); CHECK(found&&strstr(found,"cmd.exe")); free(found); found=find_executable(exe); CHECK(found); free(found); found=get_executable_dir(); CHECK(found); free(found);
 char now[40],stamp[32]; utc_now(now); run_stamp(stamp); CHECK(strstr(now,"+00:00")&&strlen(stamp)==15);
 /* Windows junctions do not require developer mode. */
 snprintf(p2,sizeof(p2),"%s/junction",deep); wchar_t command[8192],linkw[4096],targetw[4096]; CHECK(MultiByteToWideChar(CP_UTF8,0,p2,-1,linkw,4096)); CHECK(MultiByteToWideChar(CP_UTF8,0,cwd,-1,targetw,4096)); for(wchar_t *p=linkw;*p;++p)if(*p==L'/')*p=L'\\'; for(wchar_t *p=targetw;*p;++p)if(*p==L'/')*p=L'\\'; swprintf(command,8192,L"cmd.exe /d /c mklink /j \"%ls\" \"%ls\" >NUL",linkw,targetw); CHECK(_wsystem(command)==0);
 CHECK(!snapshot_tree(deep)); CHECK(!snapshot_tree(p2)); char *junction_abs=absolute_path(cwd,"临时中文/a/junction/backup.bin"); char *backup_abs=absolute_path(cwd,"backup.bin"); CHECK(junction_abs&&backup_abs&&!strcmp(junction_abs,backup_abs)); free(junction_abs); free(backup_abs);
 junction_abs=absolute_path(cwd,"临时中文/a/junction/not-yet-created/leaf"); backup_abs=absolute_path(cwd,"not-yet-created/leaf"); CHECK(junction_abs&&backup_abs&&!strcmp(junction_abs,backup_abs)); free(junction_abs); free(backup_abs);
 free(exe); puts("ALL PLATFORM TESTS PASSED"); return 0;
}
