/* ComposerLint's original GTK4 setup view. Installation stays in the Python backend. */
#define _POSIX_C_SOURCE 200809L
#include <gtk/gtk.h>
#include <json-glib/json-glib.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
#include "bundle_identity.h"

typedef struct App App;
typedef struct {
    App *app;
    GSubprocess *process;
    GByteArray *pending;
    guint readers;
    gboolean exited, probe;
    gint status;
} Job;
typedef struct { Job *job; gboolean errors; GInputStream *stream; } Reader;
struct App {
    GtkApplication *application;
    GtkWindow *window;
    GtkWidget *heading, *message, *details, *full, *text, *primary, *cancel, *retry;
    GtkWidget *progress, *stage, *copy_connection;
    GtkTextBuffer *logs;
    Job *job;
    gchar *root, *plan_id, *phase, *completion, *prefix;
    goffset log_offset;
    gboolean closing, supported, full_available;
    JsonObject *diagnostic;
    guint ticker;
};
static void read_backend_log(App *a,gboolean final);

static void phase(App *a, const char *name, const char *title, const char *message) {
    g_free(a->phase); a->phase = g_strdup(name);
    gtk_label_set_text(GTK_LABEL(a->heading), title);
    gtk_label_set_text(GTK_LABEL(a->message), message ? message : "");
}
static void append_log(App *a, const char *bytes, gssize length) {
    char *valid = g_utf8_make_valid(bytes, length);
    GtkTextIter end, begin, cutoff;
    gtk_text_buffer_get_end_iter(a->logs, &end);
    gtk_text_buffer_insert(a->logs, &end, valid, -1); g_free(valid);
    gint count = gtk_text_buffer_get_char_count(a->logs);
    if (count > 60000) {
        gtk_text_buffer_get_start_iter(a->logs, &begin);
        gtk_text_buffer_get_iter_at_offset(a->logs, &cutoff, count - 60000);
        gtk_text_buffer_delete(a->logs, &begin, &cutoff);
    }
}
static gboolean regular_hash(const char *path, const char *expected) {
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    struct stat info;
    if (fd < 0) return FALSE;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_size > 16*1024*1024) { close(fd); return FALSE; }
    GChecksum *sum = g_checksum_new(G_CHECKSUM_SHA256);
    unsigned char buffer[65536]; ssize_t n;
    while ((n = read(fd, buffer, sizeof buffer)) > 0) g_checksum_update(sum, buffer, n);
    gboolean ok = n == 0 && g_str_equal(g_checksum_get_string(sum), expected);
    close(fd); g_checksum_free(sum); return ok;
}
static const char *string(JsonObject *o, const char *key) {
    if (!json_object_has_member(o, key)) return "";
    JsonNode *n = json_object_get_member(o, key);
    return JSON_NODE_HOLDS_VALUE(n) && json_node_get_value_type(n) == G_TYPE_STRING ? json_node_get_string(n) : "";
}
static gboolean boolean(JsonObject *o, const char *key) {
    return json_object_has_member(o,key) && json_object_get_boolean_member(o,key);
}
static void set_details(App *a, const char *text) { gtk_label_set_text(GTK_LABEL(a->details), text); }
static void stop(App *a) {
    if (!a->job || g_str_equal(a->phase,"cancelling")) return;
    gtk_widget_set_sensitive(a->cancel, FALSE);
    phase(a, "cancelling", "Cancelling safely", "Waiting for the backend to stop and reap its workers. Existing working installations are preserved.");
    g_subprocess_send_signal(a->job->process, SIGTERM);
}
static void send_reply(App *a, const char *action) {
    if (!a->job || !a->plan_id) return;
    JsonBuilder *b = json_builder_new(); json_builder_begin_object(b);
    json_builder_set_member_name(b,"action"); json_builder_add_string_value(b, action);
    json_builder_set_member_name(b,"plan_id"); json_builder_add_string_value(b,a->plan_id);
    json_builder_end_object(b);
    JsonNode *node = json_builder_get_root(b);
    JsonGenerator *g = json_generator_new(); json_generator_set_root(g,node);
    gchar *text = json_generator_to_data(g,NULL), *line = g_strconcat(text,"\n",NULL);
    GError *error = NULL; gsize written;
    if (!g_output_stream_write_all(g_subprocess_get_stdin_pipe(a->job->process),line,strlen(line),&written,NULL,&error)) {
        append_log(a,error->message,-1); g_clear_error(&error); stop(a);
    } else {
        phase(a,"running","Installing and verifying","Only the actions in your approved plan are running.");
        gtk_widget_set_sensitive(a->primary,FALSE);
    }
    g_free(line); g_free(text); json_node_free(node); g_object_unref(g); g_object_unref(b);
}
static gboolean valid_plan(const char *id) {
    if (strlen(id)!=64) return FALSE;
    for (const char *p=id; *p; p++) if (!g_ascii_isxdigit(*p)) return FALSE;
    return TRUE;
}
static void receive(App *a, const char *text) {
    JsonParser *parser = json_parser_new(); GError *error = NULL;
    if (!json_parser_load_from_data(parser,text,-1,&error)) goto invalid;
    JsonNode *node=json_parser_get_root(parser);
    if (!JSON_NODE_HOLDS_OBJECT(node)) goto invalid;
    JsonObject *o=json_node_get_object(node);
    if (!json_object_has_member(o,"protocol") || json_object_get_int_member(o,"protocol")!=1) goto invalid;
    const char *kind=string(o,"type");
    if (g_str_equal(kind,"probe")) {
        a->supported=boolean(o,"supported"); a->full_available=boolean(o,"full_available");
        g_clear_pointer(&a->diagnostic,json_object_unref); a->diagnostic=json_object_new();
        const char *keys[]={"distro","distro_version","architecture","session","system_python",NULL};
        for (int i=0;keys[i];i++) {
            /* No environment dump, paths, raw logs, credentials or score data. */
            gchar *safe=g_strndup(string(o,keys[i]),64);
            g_strcanon(safe,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ",'_');
            json_object_set_string_member(a->diagnostic,keys[i],safe); g_free(safe);
        }
        if(json_object_has_member(o,"abi") && JSON_NODE_HOLDS_OBJECT(json_object_get_member(o,"abi"))) {
            JsonObject *abi=json_object_get_object_member(o,"abi");
            gchar *version=g_strndup(string(abi,"glibc"),32);g_strcanon(version,"0123456789.",'_');
            json_object_set_string_member(a->diagnostic,"glibc",version);g_free(version);
            json_object_set_boolean_member(a->diagnostic,"elf_loader",boolean(abi,"elf_loader"));
            if(json_object_has_member(abi,"libraries") && JSON_NODE_HOLDS_OBJECT(json_object_get_member(abi,"libraries"))) {
                JsonObject *libraries=json_object_get_object_member(abi,"libraries");
                const char *names[]={"c","m","stdc++","gcc_s",NULL};
                for(int i=0;names[i];i++) {
                    gchar *key=g_strconcat("runtime_",names[i],NULL);
                    json_object_set_boolean_member(a->diagnostic,key,boolean(libraries,names[i]));g_free(key);
                }
            }
        }
        if(json_object_has_member(o,"display") && JSON_NODE_HOLDS_OBJECT(json_object_get_member(o,"display"))) {
            JsonObject *display=json_object_get_object_member(o,"display");
            json_object_set_boolean_member(a->diagnostic,"display_connected",boolean(display,"available"));
        }
        if(json_object_has_member(o,"tools") && JSON_NODE_HOLDS_OBJECT(json_object_get_member(o,"tools"))) {
            JsonObject *tools=json_object_get_object_member(o,"tools");
            const char *names[]={"cc","cxx","patch","findmnt","system_uv",NULL};
            for(int i=0;names[i];i++) {
                if(!json_object_has_member(tools,names[i]) || !JSON_NODE_HOLDS_OBJECT(json_object_get_member(tools,names[i])))continue;
                JsonObject *tool=json_object_get_object_member(tools,names[i]);
                gchar *key=g_strconcat(names[i],"_available",NULL);
                json_object_set_boolean_member(a->diagnostic,key,boolean(tool,"available"));g_free(key);
                if(i<2) {
                    key=g_strconcat(names[i],"_version",NULL);
                    gchar *version=g_strndup(string(tool,"version"),32);g_strcanon(version,"0123456789.",'_');
                    json_object_set_string_member(a->diagnostic,key,version);g_free(key);g_free(version);
                }
            }
        }
        json_object_set_boolean_member(a->diagnostic,"linux_export_available",FALSE);
        phase(a,a->supported?"welcome":"unsupported",a->supported?"Choose your ComposerLint setup":"This configuration needs attention",string(o,"message"));
        gtk_widget_set_sensitive(a->full,a->full_available);
        if (!a->full_available) gtk_check_button_set_active(GTK_CHECK_BUTTON(a->text),TRUE);
        set_details(a,"Full setup builds the verified renderer locally. Text-only setup needs no compiler.\nLinux permanent export is disabled in this prerelease. No system packages are installed automatically.");
        gtk_label_set_text(GTK_LABEL(a->stage),"1 / 5 · Choose your setup");
    } else if (g_str_equal(kind,"bootstrap_plan") || g_str_equal(kind,"plan")) {
        const char *id=string(o,"plan_id"); if (!valid_plan(id)) goto invalid;
        g_free(a->plan_id); a->plan_id=g_strdup(id);
        gboolean first=g_str_equal(kind,"bootstrap_plan");
        phase(a,first?"bootstrapReview":"installReview", first?"Review private tools and source":"Review the installation plan",first?"Approve these prerequisites before any download.":"Approve the shared backend’s exact dependency and renderer plan.");
        set_details(a,string(o,"description"));
        if (!first) { g_free(a->prefix);a->prefix=g_strdup(string(o,"prefix")); a->log_offset=0; }
        gtk_button_set_label(GTK_BUTTON(a->primary),first?"Approve private tools":"Approve installation");
        gtk_widget_set_sensitive(a->primary,TRUE);
        gtk_label_set_text(GTK_LABEL(a->stage),first?"2 / 5 · Prepare private tools":"3 / 5 · Review installation");
    } else if (g_str_equal(kind,"progress")) {
        gtk_label_set_text(GTK_LABEL(a->message),string(o,"message"));
        const char *stage=string(o,"stage");
        gboolean preparing=g_str_equal(stage,"source")||g_str_equal(stage,"bootstrap")||g_str_equal(stage,"filesystem");
        gtk_label_set_text(GTK_LABEL(a->stage),preparing?"2 / 5 · Prepare private tools":"4 / 5 · Install & verify");
    } else if (g_str_equal(kind,"complete")) {
        g_free(a->completion);a->completion=g_strdup(string(o,"instructions"));
        phase(a,"finishing","Verification finished","Waiting for successful backend shutdown…");
    } else if (g_str_equal(kind,"error")) {
        phase(a,"error","Setup needs attention",string(o,"message"));
        append_log(a,string(o,"message"),-1);
    } else if (g_str_equal(kind,"cancelled")) {
        phase(a,"cancelled","Setup cancelled",string(o,"message"));
    } else goto invalid;
    g_object_unref(parser);return;
invalid:
    g_clear_error(&error);g_object_unref(parser);
    append_log(a,"Invalid setup protocol; stopping safely.\n",-1);stop(a);
}
static void finish(Job *job) {
    if (!job->exited || job->readers) return;
    App *a=job->app; read_backend_log(a,TRUE); a->job=NULL;
    gtk_widget_set_sensitive(a->cancel,FALSE);gtk_widget_set_sensitive(a->retry,TRUE);
    gtk_widget_set_sensitive(a->text,TRUE);gtk_widget_set_sensitive(a->full,a->full_available);
    if (job->status==0 && !job->probe && a->completion) {
        phase(a,"complete","ComposerLint installation verified","Your private installation is independent of this extracted setup folder.");
        set_details(a,a->completion);gtk_widget_set_sensitive(a->copy_connection,TRUE);
        gtk_label_set_text(GTK_LABEL(a->stage),"5 / 5 · Connect your client");
        gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(a->progress),1.0);
    } else if (job->probe && job->status==0) {
        gtk_button_set_label(GTK_BUTTON(a->primary),"Review prerequisites");
        gtk_widget_set_sensitive(a->primary,a->supported);
    } else if (!g_str_equal(a->phase,"cancelled") && !g_str_equal(a->phase,"cancelling") && !g_str_equal(a->phase,"error")) {
        phase(a,"error","Setup did not complete","Review the local log, copy safe diagnostics if needed, and recheck before retrying. Existing installations are preserved.");
    } else if (g_str_equal(a->phase,"cancelling")) {
        phase(a,"cancelled","Setup cancelled","Retained work will be reverified on retry. Nothing is claimed installed until all checks pass.");
    }
    json_object_set_int_member(a->diagnostic,"last_exit_code",job->status);
    g_byte_array_unref(job->pending);g_object_unref(job->process);g_free(job);
    if (a->closing) {
        if (a->ticker) {g_source_remove(a->ticker);a->ticker=0;}
        g_application_quit(G_APPLICATION(a->application));
    }
}
static void exited(GObject *source,GAsyncResult *result,gpointer data) {
    Job *job=data;GError *error=NULL;
    gboolean waited=g_subprocess_wait_finish(G_SUBPROCESS(source),result,&error);
    job->status=waited && g_subprocess_get_if_exited(job->process)?g_subprocess_get_exit_status(job->process):130;
    g_clear_error(&error);job->exited=TRUE;finish(job);
}
static void read_chunk(GObject *source,GAsyncResult *result,gpointer data) {
    Reader *reader=data;Job *job=reader->job;GError *error=NULL;
    GBytes *bytes=g_input_stream_read_bytes_finish(G_INPUT_STREAM(source),result,&error);
    gsize length=0;const char *chunk=bytes?g_bytes_get_data(bytes,&length):NULL;
    if (!bytes || !length) {
        if (error) { append_log(job->app,error->message,-1); stop(job->app); }
        if (!reader->errors && job->pending->len) { append_log(job->app,"Truncated protocol event.\n",-1);stop(job->app); }
        g_clear_error(&error);if(bytes)g_bytes_unref(bytes);g_free(reader);job->readers--;finish(job);return;
    }
    if (reader->errors) append_log(job->app,chunk,length);
    else {
        g_byte_array_append(job->pending,(const guint8*)chunk,length);
        if (job->pending->len>128*1024) { g_byte_array_set_size(job->pending,0);stop(job->app); }
        else {
            guint8 *newline;
            while ((newline=memchr(job->pending->data,'\n',job->pending->len))) {
                gsize size=(gsize)(newline-job->pending->data);
                gchar *line=g_strndup((const char*)job->pending->data,size);
                g_byte_array_remove_range(job->pending,0,size+1);receive(job->app,line);g_free(line);
            }
        }
    }
    g_bytes_unref(bytes);g_input_stream_read_bytes_async(reader->stream,4096,G_PRIORITY_DEFAULT,NULL,read_chunk,reader);
}
static void child_setup(gpointer unused) { (void)unused;setsid(); }
static void launch(App *a,gboolean probe) {
    if (a->job) return;
    gchar *helper=g_build_filename(a->root,"resources","setup_support.py",NULL);
    gchar *manifest=g_build_filename(a->root,"resources","manifest.json",NULL);
    if (!regular_hash(helper,SUPPORT_SHA256) || !regular_hash(manifest,MANIFEST_SHA256)) {
        phase(a,"error","Setup resources changed","Extract a fresh ZIP and verify its published SHA-256 checksum.");goto done;
    }
    if (!g_file_test("/usr/bin/python3",G_FILE_TEST_IS_EXECUTABLE)) {
        phase(a,"error","System Python is missing","This graphical app needs system Python 3.12+ to prepare the pinned private runtime. Ask the computer owner to install Python using the distribution’s official packages, then Recheck. No system installer was started.");goto done;
    }
    gtk_widget_set_sensitive(a->primary,FALSE);gtk_widget_set_sensitive(a->retry,FALSE);
    gtk_widget_set_sensitive(a->full,FALSE);gtk_widget_set_sensitive(a->text,FALSE);
    gtk_widget_set_sensitive(a->copy_connection,FALSE);gtk_widget_set_sensitive(a->cancel,TRUE);
    g_clear_pointer(&a->completion,g_free);g_clear_pointer(&a->plan_id,g_free);g_clear_pointer(&a->prefix,g_free);
    phase(a,"checking",probe?"Checking prerequisites":"Preparing your review","Checking source integrity and this computer’s capabilities…");
    gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(a->progress),0.0);
    GSubprocessLauncher *launcher=g_subprocess_launcher_new(G_SUBPROCESS_FLAGS_STDIN_PIPE|G_SUBPROCESS_FLAGS_STDOUT_PIPE|G_SUBPROCESS_FLAGS_STDERR_PIPE);
    gchar **environment=g_new0(gchar*,1);
    const char *allowed[]={"HOME","LANG","LC_ALL","XDG_DATA_HOME","WAYLAND_DISPLAY","DISPLAY","XAUTHORITY","XDG_RUNTIME_DIR","XDG_CURRENT_DESKTOP","SSL_CERT_FILE",NULL};
    for(int i=0;allowed[i];i++) if(g_getenv(allowed[i])) environment=g_environ_setenv(environment,allowed[i],g_getenv(allowed[i]),TRUE);
    environment=g_environ_setenv(environment,"PATH","/usr/bin:/bin",TRUE);
    environment=g_environ_setenv(environment,"PYTHONDONTWRITEBYTECODE","1",TRUE);
    g_subprocess_launcher_set_environ(launcher,environment);g_strfreev(environment);
    g_subprocess_launcher_set_child_setup(launcher,child_setup,NULL,NULL);
    const char *mode=gtk_check_button_get_active(GTK_CHECK_BUTTON(a->text))?"text":"full";
    const gchar *args[]={"/usr/bin/python3","-I","-B",helper,"--bundle",a->root,"--manifest-sha256",MANIFEST_SHA256,"--mode",mode,probe?"--probe":NULL,NULL};
    GError *error=NULL;GSubprocess *process=g_subprocess_launcher_spawnv(launcher,args,&error);g_object_unref(launcher);
    if(!process) {
        phase(a,"error","Could not start prerequisite checks",error->message);g_clear_error(&error);
        gtk_widget_set_sensitive(a->retry,TRUE);gtk_widget_set_sensitive(a->cancel,FALSE);goto done;
    }
    Job *job=g_new0(Job,1);job->app=a;job->process=process;job->probe=probe;job->readers=2;job->pending=g_byte_array_new();a->job=job;
    for(int i=0;i<2;i++) {
        Reader *reader=g_new0(Reader,1);reader->job=job;reader->errors=i==1;
        reader->stream=i?g_subprocess_get_stderr_pipe(process):g_subprocess_get_stdout_pipe(process);
        g_input_stream_read_bytes_async(reader->stream,4096,G_PRIORITY_DEFAULT,NULL,read_chunk,reader);
    }
    g_subprocess_wait_async(process,NULL,exited,job);
done:g_free(helper);g_free(manifest);
}
static void primary(GtkButton *button,gpointer data) {
    (void)button;App *a=data;
    if(g_str_equal(a->phase,"bootstrapReview"))send_reply(a,"approve_bootstrap");
    else if(g_str_equal(a->phase,"installReview"))send_reply(a,"approve");
    else if(!a->job)launch(a,FALSE);
}
static void recheck(GtkButton *b,gpointer data) {(void)b;launch(data,TRUE);}
static void cancel(GtkButton *b,gpointer data) {(void)b;stop(data);}
static gboolean closing(GtkWindow *w,gpointer data) {
    (void)w;App *a=data;
    if(!a->job) {if(a->ticker){g_source_remove(a->ticker);a->ticker=0;}return FALSE;}
    a->closing=TRUE;stop(a);return TRUE;
}
static void copy_connection(GtkButton *b,gpointer data) {
    (void)b;App *a=data;if(a->completion)gdk_clipboard_set_text(gtk_widget_get_clipboard(GTK_WIDGET(a->window)),a->completion);
}
static void copy_diagnostics(GtkButton *b,gpointer data) {
    (void)b;App *a=data;
    json_object_set_string_member(a->diagnostic,"setup_phase",a->phase);
    json_object_set_string_member(a->diagnostic,"package_manifest",MANIFEST_SHA256);
    json_object_set_int_member(a->diagnostic,"gtk_major",gtk_get_major_version());
    json_object_set_int_member(a->diagnostic,"gtk_minor",gtk_get_minor_version());
    JsonNode *node=json_node_new(JSON_NODE_OBJECT);json_node_set_object(node,a->diagnostic);
    JsonGenerator *g=json_generator_new();json_generator_set_root(g,node);json_generator_set_pretty(g,TRUE);
    gchar *text=json_generator_to_data(g,NULL);gdk_clipboard_set_text(gtk_widget_get_clipboard(GTK_WIDGET(a->window)),text);
    g_free(text);g_object_unref(g);json_node_free(node);
}
static void read_backend_log(App *a,gboolean final) {
    if(a->prefix && g_path_is_absolute(a->prefix)) {
        gchar *path=g_build_filename(a->prefix,"setup.log",NULL);int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);g_free(path);
        struct stat info;
        if(fd>=0) {
            if(!fstat(fd,&info) && S_ISREG(info.st_mode)) {
                if(final && info.st_size>a->log_offset+60000)a->log_offset=info.st_size-60000;
                char buffer[65536];ssize_t n=pread(fd,buffer,sizeof buffer,a->log_offset);
                if(n>0){append_log(a,buffer,n);a->log_offset+=n;}
            }
            close(fd);
        }
    }
}
static gboolean tick(gpointer data) {
    App *a=data;if(a->job)gtk_progress_bar_pulse(GTK_PROGRESS_BAR(a->progress));
    if(a->job)read_backend_log(a,FALSE);
    return G_SOURCE_CONTINUE;
}
static GtkWidget *label(const char *text,const char *style) {
    GtkWidget *w=gtk_label_new(text);gtk_label_set_wrap(GTK_LABEL(w),TRUE);
    gtk_label_set_wrap_mode(GTK_LABEL(w),PANGO_WRAP_WORD_CHAR);
    gtk_label_set_xalign(GTK_LABEL(w),0);if(style)gtk_widget_add_css_class(w,style);return w;
}
static GtkWidget *button(const char *title,GCallback callback,App *a) {
    GtkWidget *b=gtk_button_new_with_label(title);g_signal_connect(b,"clicked",callback,a);return b;
}
static void activate(GtkApplication *application,gpointer data) {
    App *a=data;a->application=application;a->phase=g_strdup("welcome");a->diagnostic=json_object_new();
    a->window=GTK_WINDOW(gtk_application_window_new(application));gtk_window_set_title(a->window,"ComposerLint Setup — Linux preview");gtk_window_set_default_size(a->window,1040,800);
    g_signal_connect(a->window,"close-request",G_CALLBACK(closing),a);
    const char *files[]={"setup_support.py","manifest.json","setup.css","composerlint.svg",NULL};
    const char *hashes[]={SUPPORT_SHA256,MANIFEST_SHA256,STYLE_SHA256,ICON_SHA256};
    for(int i=0;files[i];i++) {
        gchar *path=g_build_filename(a->root,"resources",files[i],NULL);
        gboolean trusted=regular_hash(path,hashes[i]);g_free(path);
        if(!trusted) {
            GtkWidget *failure=gtk_label_new("Setup resource integrity failed. Extract a fresh ZIP and verify its SHA-256 checksum. No source code was executed.");
            gtk_label_set_wrap(GTK_LABEL(failure),TRUE);gtk_window_set_child(a->window,failure);gtk_window_present(a->window);return;
        }
    }
    GtkWidget *header=gtk_header_bar_new();gtk_window_set_titlebar(a->window,header);
    gtk_header_bar_set_title_widget(GTK_HEADER_BAR(header),gtk_label_new("ComposerLint Setup"));
    GtkWidget *outer=gtk_box_new(GTK_ORIENTATION_HORIZONTAL,0);gtk_window_set_child(a->window,outer);
    GtkWidget *side=gtk_box_new(GTK_ORIENTATION_VERTICAL,24);gtk_widget_add_css_class(side,"sidebar");gtk_widget_set_size_request(side,200,-1);gtk_box_append(GTK_BOX(outer),side);
    gchar *icon=g_build_filename(a->root,"resources","composerlint.svg",NULL);GtkWidget *image=gtk_image_new_from_file(icon);g_free(icon);gtk_image_set_pixel_size(GTK_IMAGE(image),96);gtk_box_append(GTK_BOX(side),image);
    gtk_box_append(GTK_BOX(side),label("COMPOSERLINT","brand"));
    gtk_box_append(GTK_BOX(side),label("01  Choose your setup\n\n02  Prepare private tools\n\n03  Review installation\n\n04  Install & verify\n\n05  Connect your client","steps"));
    gtk_box_append(GTK_BOX(side),label("Linux volunteer preview\nArch · Ubuntu\n\nYour music stays yours.","muted"));
    GtkWidget *scroll=gtk_scrolled_window_new();gtk_widget_set_hexpand(scroll,TRUE);gtk_box_append(GTK_BOX(outer),scroll);
    GtkWidget *body=gtk_box_new(GTK_ORIENTATION_VERTICAL,16);gtk_widget_add_css_class(body,"body");gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroll),body);
    a->heading=label("Choose your ComposerLint setup","title");gtk_box_append(GTK_BOX(body),a->heading);
    a->message=label("Checking this computer…",NULL);gtk_label_set_selectable(GTK_LABEL(a->message),TRUE);gtk_box_append(GTK_BOX(body),a->message);
    a->full=gtk_check_button_new_with_label("Full experience — analysis, notation & verified temporary editing");
    a->text=gtk_check_button_new_with_label("Text analysis only — no renderer or compiler needed");
    gtk_check_button_set_group(GTK_CHECK_BUTTON(a->text),GTK_CHECK_BUTTON(a->full));gtk_check_button_set_active(GTK_CHECK_BUTTON(a->full),TRUE);
    gtk_box_append(GTK_BOX(body),a->full);gtk_box_append(GTK_BOX(body),a->text);
    GtkWidget *card=gtk_box_new(GTK_ORIENTATION_VERTICAL,8);gtk_widget_add_css_class(card,"card");gtk_box_append(GTK_BOX(body),card);
    a->details=label("",NULL);gtk_label_set_selectable(GTK_LABEL(a->details),TRUE);gtk_box_append(GTK_BOX(card),a->details);
    a->stage=label("Prerequisites","muted");gtk_box_append(GTK_BOX(body),a->stage);
    a->progress=gtk_progress_bar_new();gtk_box_append(GTK_BOX(body),a->progress);
    GtkWidget *actions=gtk_box_new(GTK_ORIENTATION_HORIZONTAL,8);gtk_box_append(GTK_BOX(body),actions);
    a->primary=button("Review prerequisites",G_CALLBACK(primary),a);gtk_widget_add_css_class(a->primary,"suggested-action");gtk_widget_set_sensitive(a->primary,FALSE);gtk_box_append(GTK_BOX(actions),a->primary);
    a->retry=button("Recheck / Retry",G_CALLBACK(recheck),a);gtk_box_append(GTK_BOX(actions),a->retry);
    a->cancel=button("Cancel safely",G_CALLBACK(cancel),a);gtk_widget_set_sensitive(a->cancel,FALSE);gtk_box_append(GTK_BOX(actions),a->cancel);
    a->copy_connection=button("Copy connection instructions",G_CALLBACK(copy_connection),a);gtk_widget_set_sensitive(a->copy_connection,FALSE);gtk_box_append(GTK_BOX(body),a->copy_connection);
    GtkWidget *diagnostics=button("Copy safe diagnostics",G_CALLBACK(copy_diagnostics),a);gtk_box_append(GTK_BOX(body),diagnostics);
    gtk_box_append(GTK_BOX(body),label("Diagnostics include platform and setup status only. Local logs below can contain local paths; they are never uploaded automatically.","muted"));
    GtkWidget *expander=gtk_expander_new("Installation details & local log");gtk_box_append(GTK_BOX(body),expander);
    GtkWidget *logs=gtk_text_view_new();gtk_text_view_set_editable(GTK_TEXT_VIEW(logs),FALSE);gtk_text_view_set_monospace(GTK_TEXT_VIEW(logs),TRUE);gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(logs),GTK_WRAP_WORD_CHAR);a->logs=gtk_text_view_get_buffer(GTK_TEXT_VIEW(logs));
    GtkWidget *log_scroll=gtk_scrolled_window_new();gtk_widget_set_size_request(log_scroll,-1,200);gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(log_scroll),logs);gtk_expander_set_child(GTK_EXPANDER(expander),log_scroll);
    gchar *css=g_build_filename(a->root,"resources","setup.css",NULL);GtkCssProvider *provider=gtk_css_provider_new();gtk_css_provider_load_from_path(provider,css);g_free(css);
    gtk_style_context_add_provider_for_display(gtk_widget_get_display(GTK_WIDGET(a->window)),GTK_STYLE_PROVIDER(provider),GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);g_object_unref(provider);
    gtk_window_present(a->window);a->ticker=g_timeout_add(250,tick,a);launch(a,TRUE);
}
int main(int argc,char **argv) {
    char executable[4096];ssize_t size=readlink("/proc/self/exe",executable,sizeof executable-1);
    if(size<=0 || size>=(ssize_t)sizeof executable-1)return 1;
    executable[size]='\0';
    App app={0};app.root=g_path_get_dirname(executable);
    GtkApplication *application=gtk_application_new("org.composerlint.setup",G_APPLICATION_NON_UNIQUE);
    g_signal_connect(application,"activate",G_CALLBACK(activate),&app);
    int result=g_application_run(G_APPLICATION(application),argc,argv);
    if(app.ticker)g_source_remove(app.ticker);
    g_object_unref(application);g_free(app.root);g_free(app.phase);g_free(app.plan_id);g_free(app.completion);g_free(app.prefix);g_clear_pointer(&app.diagnostic,json_object_unref);return result;
}
