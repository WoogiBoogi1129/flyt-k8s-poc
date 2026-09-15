package controller

import (
    "context"
    "fmt"
    "net"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)
type VMIReconciler struct{Base}
func phase(v *unstructured.Unstructured)string{s,_,_:=unstructured.NestedString(v.Object,"status","phase");return s}
func terminal(v *unstructured.Unstructured)bool{p:=phase(v);return !v.GetDeletionTimestamp().IsZero()||p=="Succeeded"||p=="Failed"}
func vmIP(v *unstructured.Unstructured)string{
    networks,_,_:=unstructured.NestedSlice(v.Object,"spec","networks");names:=map[string]bool{}
    for _,entry:=range networks{if n,ok:=entry.(map[string]interface{});ok{if _,pod:=n["pod"];pod{if name,ok:=n["name"].(string);ok{names[name]=true}}}}
    interfaces,_,_:=unstructured.NestedSlice(v.Object,"status","interfaces")
    for _,entry:=range interfaces{if i,ok:=entry.(map[string]interface{});ok{
        name,_:=i["name"].(string);ip,_:=i["ipAddress"].(string);parsed:=net.ParseIP(ip)
        if names[name]&&parsed!=nil&&parsed.To4()!=nil&&!parsed.IsLoopback()&&!parsed.IsUnspecified(){return parsed.String()}
    }};return ""
}
func (b *Base) deleteWorker(ctx context.Context,w *api.FlytWorker)error{
    if !w.DeletionTimestamp.IsZero(){return nil};uid,rv:=w.UID,w.ResourceVersion
    return client.IgnoreNotFound(b.Delete(ctx,w,&client.DeleteOptions{Preconditions:&metav1.Preconditions{UID:&uid,ResourceVersion:&rv}}))
}
func (r *VMIReconciler) Reconcile(ctx context.Context,req ctrl.Request)(ctrl.Result,error){
    v:=vmi();if err:=r.Reader.Get(ctx,req.NamespacedName,v);err!=nil{return ctrl.Result{},client.IgnoreNotFound(err)}
    w:=&api.FlytWorker{};err:=r.Reader.Get(ctx,types.NamespacedName{Namespace:v.GetNamespace(),Name:workerName(string(v.GetUID()))},w)
    exists:=err==nil;if err!=nil&&!apierrors.IsNotFound(err){return ctrl.Result{},err}
    if exists&&(w.Spec.VMIRef.UID!=string(v.GetUID())||w.Spec.VMIRef.Name!=v.GetName()){return retry()}
    if terminal(v){
        if exists{if err=r.deleteWorker(ctx,w);err!=nil{return ctrl.Result{},err};return retry()}
        _,err=r.finalizer(ctx,v,false);return ctrl.Result{},err
    }
    // A running VMI keeps its original request allocation until its lifecycle
    // ends. The Worker reconciler handles approval withdrawal/request deletion.
    if exists&&w.Spec.Request!=nil{return retry()}
    if exists{if changed,err:=r.recordLegacyVMI(ctx,v);changed||err!=nil{return ctrl.Result{Requeue:true},err}}
    if exists&&v.GetAnnotations()[RequestAnnotation]!=""{
        r.Events.Event(v,"Warning","InputConflict","Legacy Worker retained; select one input mode for the next VMI")
        return retry()
    }
    if !exists&&(v.GetAnnotations()[RequestAnnotation]!=""||v.GetAnnotations()[SnapshotAnnotation]!=""){
        return r.reconcileRequestVMI(ctx,v)
    }
    profile,cp:=v.GetAnnotations()["flyt.dev/profile"],v.GetAnnotations()["flyt.dev/control-plane"]
    opted:=profile!=""&&cp!=""
    remove:=terminal(v)||(exists&&w.Annotations["flyt.dev/created-from-vmi"]=="true"&&!opted)
    if remove{
        if exists{if err=r.deleteWorker(ctx,w);err!=nil{return ctrl.Result{},err};return retry()}
        _,err=r.finalizer(ctx,v,false);return ctrl.Result{},err
    }
    if exists{
        if w.Annotations["flyt.dev/created-from-vmi"]=="true"&&(w.Spec.ProfileRef.Name!=profile||w.Spec.ControlPlaneRef.Name!=cp){return ctrl.Result{RequeueAfter:Period},r.deleteWorker(ctx,w)}
        return retry()
    }
    if !opted{_,err=r.finalizer(ctx,v,false);return ctrl.Result{},err}
    if changed,err:=r.finalizer(ctx,v,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    if phase(v)!="Running"||vmIP(v)==""{return retry()}
    if changed,err:=r.recordLegacyVMI(ctx,v);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    p:=&api.FlytGPUProfile{};if err=r.Reader.Get(ctx,types.NamespacedName{Namespace:v.GetNamespace(),Name:profile},p);err!=nil{return ctrl.Result{},err}
    c:=&api.FlytControlPlane{};if err=r.Reader.Get(ctx,types.NamespacedName{Namespace:v.GetNamespace(),Name:cp},c);err!=nil{return ctrl.Result{},err}
    if !p.DeletionTimestamp.IsZero()||!c.DeletionTimestamp.IsZero(){return retry()}
    w=&api.FlytWorker{ObjectMeta:metav1.ObjectMeta{Name:workerName(string(v.GetUID())),Namespace:v.GetNamespace(),
        Labels:map[string]string{Experiment:Managed},Annotations:map[string]string{"flyt.dev/created-from-vmi":"true"},
        Finalizers:[]string{Finalizer},OwnerReferences:[]metav1.OwnerReference{{APIVersion:"kubevirt.io/v1",Kind:"VirtualMachineInstance",Name:v.GetName(),UID:v.GetUID(),Controller:boolp(false),BlockOwnerDeletion:boolp(false)}}},
        Spec:api.WorkerSpec{VMIRef:identity(v),ProfileRef:identity(p),ControlPlaneRef:identity(c)}}
    if err=r.Create(ctx,w);apierrors.IsAlreadyExists(err){return retry()};return ctrl.Result{},err
}

// The VMI reconciler remains the sole creator of automatic Workers in both modes.
func (r *VMIReconciler) reconcileRequestVMI(ctx context.Context,v *unstructured.Unstructured)(ctrl.Result,error){
    reject:=func(reason string,err error)(ctrl.Result,error){
        r.Events.Event(v,"Warning",reason,err.Error());return retry()
    }
    s,err:=readSnapshot(v);if err!=nil{return reject("InvalidSnapshot",err)}
    if s==nil&&v.GetAnnotations()[LegacyVMIAnnotation]==string(v.GetUID()){
        return reject("PendingRestart",fmt.Errorf("this VMI used legacy allocation; request mode requires a new VMI"))
    }
    if phase(v)!="Running"||vmIP(v)==""{return retry()}
    if changed,err:=r.finalizer(ctx,v,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    if s==nil{
        if v.GetAnnotations()["flyt.dev/profile"]!=""||v.GetAnnotations()["flyt.dev/control-plane"]!=""{
            return reject("InputConflict",fmt.Errorf("gpu-request cannot be combined with legacy profile/control-plane annotations"))
        }
        request:=&api.FlytGPURequest{}
        if err=r.Reader.Get(ctx,types.NamespacedName{Namespace:v.GetNamespace(),Name:v.GetAnnotations()[RequestAnnotation]},request);err!=nil{return reject("RequestUnavailable",err)}
        if !request.DeletionTimestamp.IsZero(){return retry()}
        if !matchesVM(v,request.Spec.VMRef){return reject("VMIdentityMismatch",fmt.Errorf("request must reference the VMI's owning VM UID"))}
        if _,err=r.requestVM(ctx,request);err!=nil{return reject("VMUnavailable",err)}
        p,cp,err:=r.requestDependencies(ctx,request);if err!=nil{return reject("DependencyUnavailable",err)}
        if !p.DeletionTimestamp.IsZero()||!cp.DeletionTimestamp.IsZero(){return retry()}
        q,err:=normalizeRequest(request.Spec);if err!=nil{return reject("InvalidRequest",err)}
        if err=withinProfile(q,p);err!=nil{return reject("RequestNotApproved",err)}
        if changed,err:=r.finalizer(ctx,request,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
        s=&requestSnapshot{VMIUID:string(v.GetUID()),Allocation:api.RequestAllocation{RequestRef:identity(request),Generation:request.Generation,
            VMRef:request.Spec.VMRef,Resources:q},ProfileRef:request.Spec.ProfileRef,ControlPlaneRef:request.Spec.ControlPlaneRef}
        return ctrl.Result{Requeue:true},r.writeSnapshot(ctx,v,s)
    }
    request,err:=r.snapshotRequest(ctx,v,s);if err!=nil{return reject("RequestUnavailable",err)}
    others,err:=r.workers(ctx,v.GetNamespace());if err!=nil{return ctrl.Result{},err}
    for _,other:=range others{if other.Spec.Request!=nil&&other.Spec.Request.VMRef==s.Allocation.VMRef&&other.Spec.VMIRef.UID!=string(v.GetUID()){
        return reject("PreviousVMIReleasing",fmt.Errorf("previous VMI Worker still exists; wait for its cleanup"))
    }}
    p,cp,err:=r.requestDependencies(ctx,request);if err!=nil{return reject("DependencyUnavailable",err)}
    if !p.DeletionTimestamp.IsZero()||!cp.DeletionTimestamp.IsZero(){return retry()}
    if err=withinProfile(s.Allocation.Resources,p);err!=nil{return reject("RequestNotApproved",err)}
    if changed,err:=r.finalizer(ctx,request,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    w:=&api.FlytWorker{ObjectMeta:metav1.ObjectMeta{Name:workerName(string(v.GetUID())),Namespace:v.GetNamespace(),
        Labels:map[string]string{Experiment:Managed},Annotations:map[string]string{"flyt.dev/created-from-vmi":"true"},
        Finalizers:[]string{Finalizer},OwnerReferences:[]metav1.OwnerReference{{APIVersion:"kubevirt.io/v1",Kind:"VirtualMachineInstance",Name:v.GetName(),UID:v.GetUID(),Controller:boolp(false),BlockOwnerDeletion:boolp(false)}}},
        Spec:api.WorkerSpec{VMIRef:identity(v),ProfileRef:s.ProfileRef,ControlPlaneRef:s.ControlPlaneRef,Request:&s.Allocation}}
    if err=r.Create(ctx,w);apierrors.IsAlreadyExists(err){return retry()};return ctrl.Result{},err
}
